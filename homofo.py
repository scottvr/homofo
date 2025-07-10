#!/usr/bin/env python3
import re
import string
import random
import requests
import pronouncing
from wordfreq import zipf_frequency
import argparse
import sys
import sqlite3
from functools import lru_cache

# ————————————————————————————————————————————
# Default tunable weights for scoring (can override via CLI)
DEFAULT_ALPHA = 1.0     # phone similarity weight
DEFAULT_BETA = 0.5      # orthographic similarity weight
DEFAULT_GAMMA = 0.2     # frequency weight
DEFAULT_LENGTH_WEIGHT = 0.0  # length preference weight
DEFAULT_MIN_ZIPF = 2.0  # minimum Zipf frequency for a candidate
DB_FILE = 'homophone_cache.db'

# Curated overrides
CURATED = {
    "nice":     ["ice", "gneiss"], "it":       ["tit"],
    "be":       ["bee", "bean"],   "see":      ["sea"],
    "read":     ["reed"],          "red":      ["read"],
    "eye":      ["I", "aye"],      "please":   ["pleas"],
    "mister":   ["missed her"],    "dunno":    ["dough no"],
    "wouldn't": ["wooden"],        "beginning":["big inning"],
}

# Phrase-level overrides
PHRASES = {
    r"\bwouldn't it\b": "wooden tit",
    r"\bit be\b":       "eat bee",
}

# Blacklist of disallowed substitutions: map original -> set of banned respellings
BLACKLIST = {
    "st": {"street"},
}

# ————————————————————————————————————————————
# Helper Functions (Stateless)
# ————————————————————————————————————————————

def setup_database(db_file):
    """Initializes the SQLite database for caching."""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS words (
        id INTEGER PRIMARY KEY,
        word TEXT UNIQUE NOT NULL
    )''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS homophone_links (
        word_id INTEGER,
        homophone_id INTEGER,
        source TEXT NOT NULL,
        FOREIGN KEY (word_id) REFERENCES words(id),
        FOREIGN KEY (homophone_id) REFERENCES words(id),
        PRIMARY KEY (word_id, homophone_id, source)
    )''')
    conn.commit()
    return conn

def edit_distance(a, b):
    """Calculates the Levenshtein distance between two strings."""
    n, m = len(a), len(b)
    if n == 0: return m
    if m == 0: return n
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): dp[i][0] = i
    for j in range(m + 1): dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp[n][m]

def phone_dist(w1, w2):
    """Calculates edit distance between the primary pronunciations of two words."""
    p1 = pronouncing.phones_for_word(w1)
    p2 = pronouncing.phones_for_word(w2)
    if not p1 or not p2: return float('inf')
    return edit_distance(p1[0].split(), p2[0].split())

def ortho_dist(w1, w2):
    """Calculates orthographic (spelling) distance."""
    return edit_distance(w1.lower(), w2.lower())

def freq_score(w):
    """Normalized frequency score."""
    return zipf_frequency(w, 'en') / 7.0

def generate_strict_homophones(word):
    """Finds strict homophones using the CMU Pronouncing Dictionary."""
    phones = pronouncing.phones_for_word(word)
    if not phones: return []
    out = []
    # Use the first pronunciation as the primary one
    for cand in pronouncing.search(f'^{re.escape(phones[0])}$'):
        if cand.lower() != word.lower() and cand.isalpha():
            out.append(cand)
    return out

def apply_phrase_overrides(text):
    """Applies hardcoded phrase substitutions."""
    for pat, repl in PHRASES.items():
        text = re.sub(pat, repl, text, flags=re.IGNORECASE)
    return text

# ————————————————————————————————————————————
# Homophone Respeech Class
# ————————————————————————————————————————————

class HomophoneRespell:
    """
    Encapsulates settings and logic for finding and substituting homophones.
    Uses a tiered caching system: LRU (in-memory) -> SQLite (persistent) -> Live lookups.
    """
    def __init__(self, db_conn, args):
        self.db_conn = db_conn
        # Behavior flags from CLI arguments
        self.strict_only = args.strict_only
        self.strict_first = args.strict_first
        self.enable_multisplit = args.multiword
        self.prefer_longer = args.prefer_longer
        self.mode = args.mode
        # Scoring weights from CLI arguments
        self.alpha = args.alpha
        self.beta = args.beta
        self.gamma = args.gamma
        self.length_weight = args.length_weight
        self.min_zipf = args.min_zipf
        # Dynamically set the cache size for the get_substitution method
        self.get_substitution = lru_cache(maxsize=args.lru_cache_size)(self.get_substitution)

    def _try_syllable_split(self, base):
        """Attempts to find a homophone for the first syllable of a word."""
        if len(base) < 6: return None
        split_point = len(base) // 3
        left, rest = base[:split_point], base[split_point:]
        # Use the main substitution logic for the first part
        sub = self.get_substitution(left)
        if sub and sub.lower() != left.lower():
            return f"{sub} {rest}"
        return None

    def _try_multiword_split(self, base):
        """Attempts to split a word into two separate words with homophones."""
        L = len(base)
        best_sub = None
        best_score = -1.0
        # Iterate through possible split points
        for i in range(2, L - 2):
            left, right = base[:i], base[i:]
            # Use the main substitution logic for both parts
            lsub = self.get_substitution(left)
            rsub = self.get_substitution(right)
            if lsub and rsub and lsub != left and rsub != right:
                score = freq_score(lsub) + freq_score(rsub)
                if score > best_score:
                    best_score = score
                    best_sub = f"{lsub} {rsub}"
        return best_sub

    # This method is decorated with lru_cache in the __init__ method
    def get_substitution(self, token):
        """
        Main logic for finding a homophone substitution for a single token.
        This method implements the full tiered caching and lookup strategy.
        """
        prefix = re.match(r"^[{}]+".format(re.escape(string.punctuation)), token)
        suffix = re.search(r"[{}]+$".format(re.escape(string.punctuation)), token)
        pfx = prefix.group(0) if prefix else ''
        sfx = suffix.group(0) if suffix else ''
        base = token.strip(string.punctuation)
        if not base: return token
        low = base.lower()

        # --- Early exit for special splits (if enabled) ---
        if self.enable_multisplit and not self.strict_only:
            mw = self._try_multiword_split(low)
            if mw: return pfx + mw + sfx

        if self.mode == 'syllable' and not self.strict_only:
            ss = self._try_syllable_split(low)
            if ss: return pfx + ss + sfx

        if low in CURATED:
            return pfx + random.choice(CURATED[low]) + sfx

        cursor = self.db_conn.cursor()
        all_candidates = set()

        # --- Tier 2: SQLite DB Cache Lookup ---
        cursor.execute("""
            SELECT w2.word, hl.source FROM words w1
            JOIN homophone_links hl ON w1.id = hl.word_id
            JOIN words w2 ON w2.id = hl.homophone_id
            WHERE w1.word = ?
        """, (low,))
        cached_results = cursor.fetchall()

        if cached_results:
            cached_cmu = {word for word, source in cached_results if source == 'cmu'}
            cached_datamuse = {word for word, source in cached_results if source == 'datamuse'}
            
            if self.strict_only:
                all_candidates.update(cached_cmu)
            elif self.strict_first:
                all_candidates.update(cached_cmu if cached_cmu else cached_datamuse)
            else:
                all_candidates.update(cached_cmu | cached_datamuse)

        # --- Tier 3: Live Lookup (if cache miss or insufficient) ---
        if not all_candidates:
            cmu_list = generate_strict_homophones(low)
            datamuse_list = []

            if not self.strict_only and (not self.strict_first or not cmu_list):
                try:
                    resp = requests.get(f"https://api.datamuse.com/words?sl={low}&max=20")
                    resp.raise_for_status()
                    datamuse_list = [entry['word'] for entry in resp.json() if entry.get('word')]
                except requests.RequestException as e:
                    print(f"API Error for word '{low}': {e}", file=sys.stderr)

            if self.strict_only:
                all_candidates.update(cmu_list)
            elif self.strict_first:
                all_candidates.update(cmu_list if cmu_list else datamuse_list)
            else:
                all_candidates.update(set(cmu_list) | set(datamuse_list))

            # --- Write new findings back to SQLite cache ---
            try:
                def get_word_id(word):
                    cursor.execute("INSERT OR IGNORE INTO words (word) VALUES (?)", (word,))
                    return cursor.execute("SELECT id FROM words WHERE word = ?", (word,)).fetchone()[0]

                def write_links(word_list, source_name):
                    if not word_list: return
                    original_id = get_word_id(low)
                    for cand_word in word_list:
                        cand_id = get_word_id(cand_word)
                        cursor.execute("INSERT OR IGNORE INTO homophone_links (word_id, homophone_id, source) VALUES (?, ?, ?)",
                                       (original_id, cand_id, source_name))
                
                write_links(cmu_list, 'cmu')
                write_links(datamuse_list, 'datamuse')
                self.db_conn.commit()
            except sqlite3.Error as e:
                print(f"Database write error: {e}", file=sys.stderr)

        # --- Candidate Filtering and Scoring ---
        all_candidates.discard(low)
        if not all_candidates: return token

        primary = {w for w in all_candidates if zipf_frequency(w, 'en') >= self.min_zipf and len(w) > 1}
        filtered = primary if (self.strict_only or primary) else {w for w in all_candidates if len(w) > 1}

        banned = BLACKLIST.get(low, set())
        filtered = {w for w in filtered if w not in banned}
        if not filtered: return token

        max_len = max(len(w) for w in filtered) if self.prefer_longer and filtered else 1.0
        best_cand, best_score = None, -1.0
        for w in filtered:
            pd = phone_dist(low, w)
            od = ortho_dist(low, w)
            fs = freq_score(w)
            len_term = (len(w) / max_len) if self.prefer_longer else 0.0
            score = self.alpha * (1 / (1 + pd)) + self.beta * (1 / (1 + od)) + self.gamma * fs + self.length_weight * len_term
            if score > best_score:
                best_score, best_cand = score, w

        return pfx + best_cand + sfx if best_cand else token

# ————————————————————————————————————————————
# Main Execution
# ————————————————————————————————————————————

def main():
    """Main function to parse arguments and process the text file."""
    parser = argparse.ArgumentParser(
        description="Homophonic respeller with tiered caching.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    # File I/O
    parser.add_argument("input_file", help="Path to input text file.")
    parser.add_argument("output_file", nargs='?', default=None, help="Output file (defaults to stdout).")
    parser.add_argument("--chunk-size", type=int, default=1000, help="Number of tokens to process at a time.")
    # Caching
    parser.add_argument("--lru-cache-size", type=int, default=2048, help="Size of the in-memory LRU cache for words.")
    # Behavior Flags
    parser.add_argument("--strict-only", action='store_true', help="Use only strict homophones from CMU dictionary.")
    parser.add_argument("--strict-first", action='store_true', help="Prioritize strict homophones before using Datamuse API.")
    parser.add_argument("--multiword", action='store_true', help="Enable multi-word splits (e.g., 'mister' -> 'missed her').")
    parser.add_argument("--prefer-longer", action='store_true', help="Prefer longer homophone candidates.")
    parser.add_argument("--mode", choices=['word', 'syllable'], default='word', help="Processing mode.")
    # Scoring Weights
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA, help="Weight for phonetic similarity.")
    parser.add_argument("--beta", type=float, default=DEFAULT_BETA, help="Weight for orthographic similarity.")
    parser.add_argument("--gamma", type=float, default=DEFAULT_GAMMA, help="Weight for word frequency.")
    parser.add_argument("--length-weight", type=float, default=DEFAULT_LENGTH_WEIGHT, help="Weight for candidate length.")
    parser.add_argument("--min-zipf", type=float, default=DEFAULT_MIN_ZIPF, help="Minimum Zipf frequency for a candidate to be considered a real word.")
    
    args = parser.parse_args()

    db_conn = None
    writer = None
    try:
        db_conn = setup_database(DB_FILE)
        respeller = HomophoneRespell(db_conn, args)

        print("Reading input file...", file=sys.stderr)
        with open(args.input_file, 'r', encoding='utf-8') as infile:
            content = infile.read()
        
        all_tokens = re.findall(r"[\w']+|\s+|[^\w\s]", content)
        total_tokens = len(all_tokens)
        print(f"Tokenizing complete. Processing {total_tokens} tokens in chunks of {args.chunk_size}.", file=sys.stderr)

        writer = open(args.output_file, 'w', encoding='utf-8') if args.output_file else sys.stdout

        # Define a simple processing function that uses the respeller instance
        def get_respelling(text):
            tokens = re.findall(r"[\w']+|\s+|[^\w\s]", text)
            out = []
            for tok in tokens:
                if tok.isalpha():
                    is_title = tok.istitle()
                    is_upper = tok.isupper()
                    # Call the cached method on the instance
                    sub = respeller.get_substitution(tok.lower())
                    if sub and sub.lower() != tok.lower():
                        sub = sub.upper() if is_upper else (sub.capitalize() if is_title else sub)
                        out.append(sub)
                    else:
                        out.append(tok)
                else:
                    out.append(tok)
            return ''.join(out)

        # Process the file in chunks
        for i in range(0, total_tokens, args.chunk_size):
            token_chunk = all_tokens[i:i + args.chunk_size]
            text_chunk = "".join(token_chunk)
            
            processed_text = apply_phrase_overrides(text_chunk)
            processed_chunk = get_respelling(processed_text)
            
            writer.write(processed_chunk)
            percent_done = min(((i + args.chunk_size) / total_tokens) * 100, 100)
            print(f"  Processed up to token {min(i + args.chunk_size, total_tokens)}... ({percent_done:.1f}%)", file=sys.stderr)

        print("Processing complete.", file=sys.stderr)
        # Print cache info
        print(respeller.get_substitution.cache_info(), file=sys.stderr)

    except FileNotFoundError:
        print(f"Error: Input file not found at '{args.input_file}'", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if db_conn:
            db_conn.close()
        if writer and writer is not sys.stdout:
            writer.close()

if __name__ == '__main__':
    main()
