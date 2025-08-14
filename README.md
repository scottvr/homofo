## Whitepaper: Methods and Findings
We evaluated the Copyleaks plagiarism detection platform’s ability to detect phonetically and semantically equivalent text that differs orthographically from the original source. To construct the test corpus, we applied automated transformations using the PASTAL and homofo.py tools to selected copyrighted works, producing outputs that maintained original meaning and phonetic similarity while replacing key words with homophones, synonyms, or grammatical mutations.

The transformed texts were submitted to Copyleaks’ web interface and API under standard configuration, and the detection scores were recorded. Copyleaks’ own marketing materials cite accuracy rates above 99 % on “paraphrased and disguised” text. In contrast, our trials yielded detection rates as low as 0 %, with multiple transformed works passing undetected despite maintaining near-verbatim semantic and phonetic equivalence to the originals.

These findings indicate that Copyleaks’ detection algorithms may rely heavily on direct lexical matching and fail to adequately capture semantically preserved content when surface orthography is significantly altered. This suggests that the system’s claimed accuracy rates may not generalize to adversarial text transformations, particularly those exploiting phonetic similarity and subtle grammatical restructuring.

[Whitepaper - Part_of_Your_World-Bypassing_Copyright_Filters_Through_Homophony_and_Semantic_Mutation.pdf](Part_of_Your_World-Bypassing_Copyright_Filters_Through_Homophony_and_Semantic_Mutation.pdf)

# homofo – Homophonic Respeller

Transform ordinary English text into "creative" respellings. Bypass copyright filters in AI models (LLM, TTS, music genAI, etc.) general AInarchy.


#### Overview  
homofo reads an input text (file or stdin), tokenizes it into words, punctuation, and whitespace, and replaces each word with a homophonic alternative. It supports:

- **Strict CMU-Dict homophones** (via the `pronouncing` library)  
- **“Sounds-like” fallbacks** (via Datamuse API)  
- **Syllable-level splits** (`--mode syllable`)  
- **Two-word splits** (`--multiword`, e.g. `purple`→`per pill`)  
- **Curated overrides** for your favorite puns (e.g. “nice”→“gneiss”)

Replacements are **scored** by a weighted combination of:
1. **Phonetic distance** (ALPHA)  
2. **Spelling distance** (BETA)  
3. **Word frequency** (GAMMA + MIN_ZIPF)  
4. **Optional length bonus** (LENGTH_WEIGHT)
### Caching and Performance

homofo uses a tiered caching system to maximize performance and build an increasingly rich network of phonetic relationships over time.

1.  **Tier 1: In-Memory LRU Cache**
    * **What it is:** A "Least Recently Used" cache that stores the most recent word substitutions directly in memory.
    * **Purpose:** Provides instantaneous lookups for words that appear frequently within a single run, dramatically speeding up the processing of large texts.
    * **Control:** The size of this cache can be adjusted with the `--lru-cache-size` command-line argument.

2.  **Tier 2: Persistent SQLite Database (`homophone_cache.db`)**
    * **What it is:** A local database file that stores all homophone relationships discovered across all runs.
    * **Purpose:** Eliminates the need for repeated API calls for the same words in future sessions. Once a word's homophones are looked up, they are saved permanently.

#### How the Database Enriches Connections

The database doesn't just store results; it creates a rich, interconnected graph of phonetic relationships. The schema is simple but powerful:

* `words`: A table of unique words.
* `homophone_links`: A table linking two words together, crucially storing the `source` of the link (`cmu` for strict homophones or `datamuse` for "sounds-like" matches).

By caching results from **both** sources, the tool builds connections that wouldn't be possible with a single source. For example:

* You look up the word `awesome`. Datamuse might return `possum` as a "sounds-like" match. This link is cached.
* Later, you look up `possum`. The CMU dictionary might find a strict homophone, `possume`.
* Now, the database implicitly links `awesome` -> `possum` -> `possume`.

Over time, this allows homofo to discover and leverage a much wider and more creative set of phonetic substitutions than either the CMU dictionary or the Datamuse API could provide alone.

#### Installation  
```bash
git clone https://github.com/scottvr/homofo.git
cd homofo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### Usage  
```bash
python homofo.py [OPTIONS] INPUT_FILE [OUTPUT_FILE]
```
- If `OUTPUT_FILE` is omitted, output is printed to stdout.

#### CLI Options

```text
--mode {word|syllable}     Tokenization mode (default: word)
--multiword                Enable two-word splits (e.g. purple→per pill)
--strict-only              Only use CMU-dict homophones; skip Datamuse
--prefer-longer            Bias toward longer respellings
--alpha FLOAT              Phone-similarity weight (default: 1.0)
--beta FLOAT               Orthographic-similarity weight (default: 0.5)
--gamma FLOAT              Frequency weight (default: 0.2)
--min-zipf FLOAT           Hard cutoff frequency (default: 2.0)
--length-weight FLOAT      Length bonus weight (default: 0.0)
```

#### Key Scoring Parameters

1. **ALPHA (phone similarity weight)**  
   - **High** → very tight phonetic matches (e.g. “sea” for “see”)  
   - **Low**  → allows looser sound-alikes  

2. **BETA (orthographic similarity weight)**  
   - **High** → favors respellings that look like the original (“knight”→“night”)  
   - **Low**  → ignores spelling similarity  

3. **GAMMA (frequency weight)**  
   - **What it does:** Gives a boost to candidates based on how common they are (Zipf frequency).  
   - **High** → strongly prefer everyday words (“sea” > “c”)  
   - **Low**  → let rare/obscure homophones (“gneiss”) compete  

4. **MIN_ZIPF (hard frequency cutoff)**  
   - **What it does:** Filters out any candidate whose Zipf score is below this threshold (≈ occurrences per million).  
   - **Effect:** Ensures all outputs are real, reasonably common words before scoring.  
   - **Interaction with GAMMA:**  
     - `MIN_ZIPF` prunes the candidate list up front;  
     - `GAMMA` then ranks that pruned list by frequency.  

5. **LENGTH_WEIGHT (length bonus)**  
   - **What it does:** Adds a normalized bonus proportional to a candidate’s length, so multi-syllable or multi-word respellings (e.g. “bean” vs “bee”, or “per pill” vs “per”) can win.  

---

#### Example

```bash
python homofo.py \
  --mode syllable \
  --multiword \
  --strict-only \
  --prefer-longer \
  --alpha 0.5 \
  --beta 0.3 \
  --gamma 0.4 \
  --min-zipf 2.5 \
  --length-weight 0.3 \
  input.txt \
  output.txt
```

- **`--mode syllable`** attempts splits like `beginning`→`big inning`  
- **`--multiword`** tries full two-word puns like `purple`→`per pill`  
- **`--strict-only`** skips any Datamuse “sounds-like” suggestions  
- **`--prefer-longer`** + `--length-weight 0.3` favors longer respellings  
- **`--gamma 0.4`** + `--min-zipf 2.5` ensures only common words are used and that frequency strongly influences choice  

Experiment with these knobs to craft anything from near-perfect phonetic clones to ludicrously absurd puns!

# Reversibility
The respelling process is mostly reversible, meaning you can take the output and convert it back to the original text using the same homophone mappings. However, some transformations may lose information (e.g., "knight"→"night") or introduce ambiguity (e.g., "sea"→"see").

Because the search is phoneme-driven, and the set of viable homophones per token is relatively narrow, re-running the “gibberish” through the model tends to return to stable attractors — often the original word or near-synonyms.

Essentially, this is round-trip lossy compression of language with a fuzzy codec.

Example Input:
```text
so you don't understand me when i write this way?
```

Transformed Output:
```text
sew yu don't understands mi wen ai rite thus wy?
```

Doubly-transformed Output:
```text
so you don't understand me when aye write this way?
```


## More Example Outputs

```text
People hayes aul an mai braun
Lightly, thing adjust don't seam they sahm
Actin' fanny butt AI don't no wai
'Scuse mi wile AI kis they skye
```

```text
THEY FURST BACK EAVE MOISES, CULLED
GENEROUS


CHAPTERS 1
1 Inn they beginnings Goad creates they heavens end they raw.
2 End they raw ways walkout for, end avoid; end harkness ways apon they faze eave they depp. End they Spirits eave Goad move apon they faze eave they walters.
3 End Goad sid, Lett their bee lite: end their ways lite.
4 End Goad sow they lite, thought tit ways goode: end Goad derided they lite frum they harkness.
5 End Goad culled they lite Daye, end they harkness hee culled Knight. End they evenings end they mourning her they furst daye.
6 AAHÂ¶ End Goad sid, Lett their bean ay permanent inn they amidst eave they walters, end lett tit divides they walters frum they walters.
7 End Goad maid they permanent, end derided they walters witch her ender they permanent frum they walters witch her abuzz they permanent: end tit ways sew.
8 End Goad culled they permanent Heavens. End they evenings end they mourning her they seconds daye.
```

#### License  
MIT © 2025 Scott VanRavenswaay (with help from chatgpt-4o-mini-high)
