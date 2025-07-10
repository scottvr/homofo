import re
import string
import random
import requests  # for Datamuse fallback
import pronouncing  # pip install pronouncing
from wordfreq import zipf_frequency  # pip install wordfreq
import argparse
import sys
import jellyfish
import epitran

class PhoneticScorer:
    """
    Calculates a weighted phonetic similarity score using multiple algorithms.
    Inspired by the approach in pastal.py.
    """
    def __init__(self):
        try:
            self.epi = epitran.Epitran("eng-Latn")
        except Exception:
            self.epi = None
            print("Warning: `epitran` not found. IPA similarity will be disabled.", file=sys.stderr)
        
        self.jellyfish = jellyfish
        self.cache = {} #

    def calculate_similarity(self, word1: str, word2: str) -> float:
        """
        Calculates a composite phonetic score between two words.
        """
        if (word1, word2) in self.cache:
            return self.cache[(word1, word2)]
        
        if not self.epi:
            # Fallback if epitran is missing
            return 1.0 / (1.0 + self.jellyfish.levenshtein_distance(word1, word2))

        # Get phonetic representations from different algorithms
        soundex1, soundex2 = self.jellyfish.soundex(word1), self.jellyfish.soundex(word2)
        meta1, meta2 = self.jellyfish.metaphone(word1), self.jellyfish.metaphone(word2)
        ipa1 = self.epi.transliterate(word1)
        ipa2 = self.epi.transliterate(word2)

        # Calculate individual similarity scores
        soundex_match = 1.0 if soundex1 == soundex2 else 0.0
        metaphone_match = 1.0 if meta1 == meta2 else 0.0
        ipa_similarity = self.jellyfish.jaro_winkler_similarity(ipa1, ipa2)

        # Weighted average for the final score
        score = (0.2 * soundex_match) + (0.3 * metaphone_match) + (0.5 * ipa_similarity)
        
        self.cache[(word1, word2)] = score
        return score

# Instantiate the scorer globally to maintain the cache across all calls in the script.
phonetic_scorer = PhoneticScorer()


# ————————————————————————————————————————————
# Default tunable weights for scoring (can override via CLI)
ALPHA = 1.0     # phone similarity weight
BETA = 0.5      # orthographic similarity weight
GAMMA = 0.2     # frequency weight
LENGTH_WEIGHT = 0.0  # length preference weight

# Minimum zipf frequency for a candidate to be counted as real English
MIN_ZIPF = 2.0  # ~ once per million

# Tokenization mode: 'word' or 'syllable'
MODE = 'word'

# Flags
STRICT_ONLY = False        # only strict CMU homophones
PREFER_LONGER = False      # prefer longer candidates
ENABLE_MULTISPLIT = False  # try splitting words into two homophones

# Curated overrides TEST
CURATED = {
    "nice":   ["ice", "gneiss"],
    "it":     ["tit"],
    "be":     ["bee", "bean"],
    "see":    ["sea"],
    "read":   ["reed"],
    "red":    ["read"],
    "eye":    ["I", "aye"],
    "please": ["pleas"],
    "mister": ["missed her"],
    "dunno": ["dough no"],
    "wouldn't": ["wooden"],
    "beginning": ["big inning"],
}

# Phrase-level overrides
PHRASES = {
    r"\bwouldn't it\b": "wooden tit",
    r"\bit be\b":       "eat bee",
}

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


def get_homophone_substitution(token):
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
    if MODE == 'syllable':
        ss = try_syllable_split(low)
        if ss: return pfx + ss + sfx

    # curated overrides
    if low in CURATED:
        return pfx + random.choice(CURATED[low]) + sfx

    # build candidates
    candidates = set(generate_strict_homophones(low))
    if not STRICT_ONLY:
        try:
            resp = requests.get(f"https://api.datamuse.com/words?sl={low}&max=20")
            resp.raise_for_status()
            for entry in resp.json():
                w = entry['word']
                if w.isalpha() and w.lower() != low:
                    candidates.add(w)
        except:
            pass

    # filter real English
    primary = {w for w in candidates if zipf_frequency(w, 'en') >= MIN_ZIPF and len(w) > 1}

    if STRICT_ONLY:
        # in strict-only mode, only keep those meeting MIN_ZIPF, otherwise give up
        filtered = primary
        if not filtered:
            return token
    else:
        # non-strict: fallback to any len>1 if none meet frequency
        filtered = primary if primary else {w for w in candidates if len(w) > 1}

    if not filtered:
        return token

    # scoring
    max_len = max(len(w) for w in filtered)
    best = None; best_score = -1.0
    for w in filtered:
        pd = phone_dist(low, w)
        od = ortho_dist(low, w)
        fs = freq_score(w)
        length_term = (len(w)/max_len) if PREFER_LONGER else 0.0
        score = ALPHA*(1/(1+pd)) + BETA*(1/(1+od)) + GAMMA*fs + LENGTH_WEIGHT*length_term
        if score > best_score:
            best_score, best = score, w
    return pfx + best_candidate + sfx


def homophonic_respelling(text):
    text = apply_phrase_overrides(text)
    tokens = re.findall(r"[\w']+|\s+|[^\w\s]", text)
    out = []
    for tok in tokens:
        if tok.isalpha():
            is_title = tok.istitle(); is_upper = tok.isupper()
            sub = get_homophone_substitution(tok)
            if sub:
                sub = sub.upper() if is_upper else (sub.capitalize() if is_title else sub)
                out.append(sub)
            else:
                out.append(tok)
        else:
            out.append(tok)
    return ''.join(out)


def main():
    global STRICT_ONLY, MODE, PREFER_LONGER, ENABLE_MULTISPLIT, ALPHA, BETA, GAMMA, LENGTH_WEIGHT, MIN_ZIPF
    parser = argparse.ArgumentParser(description="Homophonic respeller CLI")
    parser.add_argument("input_file", help="Path to input text file")
    parser.add_argument("output_file", nargs='?', default=None, help="Output file (defaults to stdout)")
    parser.add_argument("--strict-only", action='store_true', help="Use only strict homophones")
    parser.add_argument("--mode", choices=['word','syllable'], default='word', help="Tokenization mode")
    parser.add_argument("--prefer-longer", action='store_true', help="Prefer longer homophones")
    parser.add_argument("--multiword", action='store_true', help="Enable multi-word splits")
    parser.add_argument("--alpha", type=float, default=ALPHA, help="Phone similarity weight")
    parser.add_argument("--beta", type=float, default=BETA, help="Orthographic similarity weight")
    parser.add_argument("--gamma", type=float, default=GAMMA, help="Frequency weight")
    parser.add_argument("--length-weight", type=float, default=LENGTH_WEIGHT, help="Length preference weight")
    parser.add_argument("--min-zipf", type=float, default=MIN_ZIPF, help="Minimum Zipf frequency")
    args = parser.parse_args()

    STRICT_ONLY = args.strict_only
    MODE = args.mode
    PREFER_LONGER = args.prefer_longer
    ENABLE_MULTISPLIT = args.multiword
    ALPHA = args.alpha
    BETA = args.beta
    GAMMA = args.gamma
    LENGTH_WEIGHT = args.length_weight

    try:
        infile = open(args.input_file, encoding='utf-8')
    except Exception as e:
        print(f"Error opening input: {e}", file=sys.stderr)
        sys.exit(1)

    writer = open(args.output_file, 'w', encoding='utf-8') if args.output_file else sys.stdout
    for line in infile:
        writer.write(homophonic_respelling(line))
    infile.close()
    if args.output_file:
        writer.close()

if __name__ == '__main__':
    main()
