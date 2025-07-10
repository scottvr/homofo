import re
import string
import random
import requests  # for Datamuse fallback
import pronouncing  # pip install pronouncing
from wordfreq import zipf_frequency  # pip install wordfreq
import argparse
import sys
import sqlite3
import os

# ————————————————————————————————————————————
# Default tunable weights for scoring (can override via CLI)
ALPHA = 1.0     # phone similarity weight
BETA = 0.5      # orthographic similarity weight
GAMMA = 0.2     # frequency weight
LENGTH_WEIGHT = 0.0  # length preference weight
MIN_ZIPF = 2.0  # minimum Zipf frequency for a candidate to be counted as real English

# Tokenization mode: 'word' or 'syllable'
MODE = 'word'

# Flags
STRICT_ONLY = False        # only strict CMU homophones
PREFER_LONGER = False      # prefer longer candidates
ENABLE_MULTISPLIT = False  # try splitting words into two homophones

# Database file for Datamuse cache
DB_FILE = 'homophone_cache.db'

# Curated overrides TEST
CURATED = {
    "nice":     ["ice", "gneiss"],
    "it":       ["tit"],
    "be":       ["bee", "bean"],
    "see":      ["sea"],
    "read":     ["reed"],
    "red":      ["read"],
    "eye":      ["I", "aye"],
    "please":   ["pleas"],
    "mister":   ["missed her"],
    "dunno":    ["dough no"],
    "wouldn't": ["wooden"],
    "beginning":["big inning"],
}

# Phrase-level overrides
PHRASES = {
    r"\bwouldn't it\b": "wooden tit",
    r"\bit be\b":       "eat bee",
}

# Blacklist of disallowed substitutions: map original -> set of banned respellings
BLACKLIST = {
    "st": {"street"},
    # add more entries: "word": {"badsub1", "badsub2"}
}

# ————————————————————————————————————————————
# Database Setup
# ————————————————————————————————————————————

def setup_database(db_file):
    """Initializes a streamlined SQLite database for reciprocal lookups."""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    # A single, central table for all unique words
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS words (
        id INTEGER PRIMARY KEY,
        word TEXT UNIQUE NOT NULL
    )''')
    # A table to link words that are homophones of each other
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS homophone_links (
        word_id INTEGER,
        homophone_id INTEGER,
        FOREIGN KEY (word_id) REFERENCES words(id),
        FOREIGN KEY (homophone_id) REFERENCES words(id),
        PRIMARY KEY (word_id, homophone_id)
    )''')
    conn.commit()
    return conn

# ————————————————————————————————————————————

def edit_distance(a, b):
    n, m = len(a), len(b)
    if n == 0: return m
    if m == 0: return n
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): dp[i][0] = i
    for j in range(m + 1): dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost
            )
    return dp[n][m]


def phone_dist(w1, w2):
    p1 = pronouncing.phones_for_word(w1)
    p2 = pronouncing.phones_for_word(w2)
    if not p1 or not p2: return float('inf')
    return edit_distance(p1[0].split(), p2[0].split())


def ortho_dist(w1, w2):
    return edit_distance(w1.lower(), w2.lower())


def freq_score(w):
    return zipf_frequency(w, 'en') / 7.0

# ————————————————————————————————————————————

def apply_phrase_overrides(text):
    for pat, repl in PHRASES.items():
        text = re.sub(pat, repl, text, flags=re.IGNORECASE)
    return text


def generate_strict_homophones(word):
    phones = pronouncing.phones_for_word(word)
    if not phones: return []
    out = []
    for ph in phones:
        for cand in pronouncing.search('^' + re.escape(ph) + '$'):
            if cand.lower() != word.lower() and cand.isalpha():
                out.append(cand)
    return out


def get_strict_sub(base):
    low = base.lower()
    if low in CURATED: return random.choice(CURATED[low])
    cands = set(generate_strict_homophones(low))
    filtered = {w for w in cands if zipf_frequency(w, 'en') >= MIN_ZIPF and len(w) > 1}
    if not filtered:
        filtered = {w for w in cands if len(w) > 1}
    if filtered:
        return max(filtered, key=lambda w: zipf_frequency(w, 'en'))
    return None


def try_syllable_split(base):
    L = len(base)
    if L < 6: return None
    split = L // 3
    left, rest = base[:split], base[split:]
    sub = get_strict_sub(left)
    if sub and sub.lower() != left.lower():
        return f"{sub} {rest}"
    return None


def try_multiword_split(base):
    L = len(base)
    best = None; best_score = -1.0
    for i in range(2, L - 1):
        left, right = base[:i], base[i:]
        lsub = get_strict_sub(left)
        rsub = get_strict_sub(right)
        if lsub and rsub:
            score = freq_score(lsub) + freq_score(rsub)
            if score > best_score:
                best_score, best = score, f"{lsub} {rsub}"
    return best


def get_homophone_substitution(token, db_conn):
    # preserve punctuation
    prefix = re.match(r"^[{}]+".format(re.escape(string.punctuation)), token)
    suffix = re.search(r"[{}]+$".format(re.escape(string.punctuation)), token)
    pfx = prefix.group(0) if prefix else ''
    sfx = suffix.group(0) if suffix else ''
    base = token.strip(string.punctuation)
    if not base: return None
    low = base.lower()

    # multi-word split (skip under strict-only)
    if ENABLE_MULTISPLIT and not STRICT_ONLY:
        mw = try_multiword_split(low)
        if mw:
            return pfx + mw + sfx

    # syllable split (skip under strict-only)
    if MODE == 'syllable' and not STRICT_ONLY:
        ss = try_syllable_split(low)
        if ss:
            return pfx + ss + sfx

    # curated overrides
    if low in CURATED:
        return pfx + random.choice(CURATED[low]) + sfx

    cursor = db_conn.cursor()
    
    # Query the cache for existing links
    cursor.execute("""
        SELECT w2.word
        FROM words w1
        JOIN homophone_links hl ON w1.id = hl.word_id
        JOIN words w2 ON w2.id = hl.homophone_id
        WHERE w1.word = ?
    """, (low,))
    cached_results = cursor.fetchall()
    
    # Use a combined list of candidates for processing
    all_candidates = set()

    if cached_results:
        # If we have cached results, use them.
        all_candidates.update(row[0] for row in cached_results)
    else:
        # --- CACHE MISS: Get candidates from ALL sources and cache them ---
        
        # 1. Get candidates from Datamuse API
        datamuse_list = []
        try:
            resp = requests.get(f"https://api.datamuse.com/words?sl={low}&max=20")
            resp.raise_for_status()
            datamuse_list = [entry['word'] for entry in resp.json() if entry.get('word')]
        except requests.RequestException as e:
            print(f"API Error for word '{low}': {e}", file=sys.stderr)
        
        # 2. Get candidates from local CMUdict
        cmu_list = generate_strict_homophones(low)
        
        # 3. Combine all new candidates into a single set
        new_candidates_to_cache = set(datamuse_list + cmu_list)
        all_candidates.update(new_candidates_to_cache) # Use this combined set now

        # 4. Write the combined set to the database
        if new_candidates_to_cache:
            try:
                def get_word_id(word):
                    cursor.execute("INSERT OR IGNORE INTO words (word) VALUES (?)", (word,))
                    return cursor.execute("SELECT id FROM words WHERE word = ?", (word,)).fetchone()[0]

                original_word_id = get_word_id(low)

                for cand_word in new_candidates_to_cache:
                    candidate_id = get_word_id(cand_word)
                    
                    # Store forward and reverse links
                    cursor.execute("INSERT OR IGNORE INTO homophone_links (word_id, homophone_id) VALUES (?, ?)", 
                                   (original_word_id, candidate_id))
                    cursor.execute("INSERT OR IGNORE INTO homophone_links (word_id, homophone_id) VALUES (?, ?)", 
                                   (candidate_id, original_word_id))
                db_conn.commit()
            except sqlite3.Error as e:
                print(f"Database write error: {e}", file=sys.stderr)

    # The rest of the function now operates on the 'all_candidates' set,
    # which is sourced from the cache or from the newly combined API/CMU lists.
    
    # Remove the original word from the candidate list
    all_candidates.discard(low)
    if not all_candidates:
        return token


    # filter real English by frequency cutoff
    primary = {w for w in all_candidates if zipf_frequency(w, 'en') >= MIN_ZIPF and len(w) > 1}
    if STRICT_ONLY:
        filtered = primary
        if not filtered:
            return token
    else:
        filtered = primary if primary else {w for w in all_candidates if len(w) > 1}

    if not filtered:
        return token

    # apply blacklist
    banned = BLACKLIST.get(low, set())
    filtered = {w for w in filtered if w not in banned}
    if not filtered:
        return token

    # scoring
    max_len = max(len(w) for w in filtered) if filtered else 1
    best = None; best_score = -1.0
    for w in filtered:
        pd = phone_dist(low, w)
        od = ortho_dist(low, w)
        fs = freq_score(w)
        length_term = (len(w)/max_len) if PREFER_LONGER else 0.0
        score = ALPHA*(1/(1+pd)) + BETA*(1/(1+od)) + GAMMA*fs + LENGTH_WEIGHT*length_term
        if score > best_score:
            best_score, best = score, w
    return pfx + best + sfx if best else token


def homophonic_respelling(text, db_conn):
    text = apply_phrase_overrides(text)
    tokens = re.findall(r"[\w']+|\s+|[^\w\s]", text)
    out = []
    for tok in tokens:
        if tok.isalpha():
            is_title = tok.istitle(); is_upper = tok.isupper()
            # Pass the db_conn to the substitution function
            sub = get_homophone_substitution(tok, db_conn)
            if sub and sub != tok:
                sub = sub.upper() if is_upper else (sub.capitalize() if is_title else sub)
                out.append(sub)
            else:
                out.append(tok)
        else:
            out.append(tok)
    return ''.join(out)


def main():
    global STRICT_ONLY, MODE, PREFER_LONGER, ENABLE_MULTISPLIT
    global ALPHA, BETA, GAMMA, LENGTH_WEIGHT, MIN_ZIPF

    parser = argparse.ArgumentParser(description="Homophonic respeller CLI")
    parser.add_argument("input_file", help="Path to input text file")
    parser.add_argument("output_file", nargs='?', default=None,
                        help="Output file (defaults to stdout)")
    parser.add_argument("--strict-only", action='store_true',
                        help="Use only strict homophones")
    parser.add_argument("--mode", choices=['word','syllable'], default='word',
                        help="Tokenization mode")
    parser.add_argument("--prefer-longer", action='store_true',
                        help="Prefer longer homophones")
    parser.add_argument("--multiword", action='store_true',
                        help="Enable multi-word splits")
    parser.add_argument("--alpha", type=float, default=ALPHA,
                        help="Phone similarity weight")
    parser.add_argument("--beta", type=float, default=BETA,
                        help="Orthographic similarity weight")
    parser.add_argument("--gamma", type=float, default=GAMMA,
                        help="Frequency weight")
    parser.add_argument("--length-weight", type=float, default=LENGTH_WEIGHT,
                        help="Length preference weight")
    parser.add_argument("--min-zipf", type=float, default=MIN_ZIPF,
                        help="Minimum Zipf frequency")
    args = parser.parse_args()

    STRICT_ONLY = args.strict_only
    MODE = args.mode
    PREFER_LONGER = args.prefer_longer
    ENABLE_MULTISPLIT = args.multiword
    ALPHA = args.alpha
    BETA = args.beta
    GAMMA = args.gamma
    LENGTH_WEIGHT = args.length_weight
    MIN_ZIPF = args.min_zipf

    db_conn = None
    try:
        db_conn = setup_database(DB_FILE)
        infile = open(args.input_file, encoding='utf-8')
    except Exception as e:
        print(f"Error opening files: {e}", file=sys.stderr)
        if db_conn:
            db_conn.close()
        sys.exit(1)

    writer = open(args.output_file, 'w', encoding='utf-8') if args.output_file else sys.stdout
    for line in infile:
        # Pass the database connection through the call chain
        writer.write(homophonic_respelling(line, db_conn))
        
    infile.close()
    if args.output_file:
        writer.close()
    if db_conn:
        db_conn.close()

if __name__ == '__main__':
    main()