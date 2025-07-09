# HoMofo – Homophonic Respeller

Transform ordinary English text into "creative" respellings. Bypass copyright filters in AI models, etc. general AInarchy.

#### Overview  
HoMofo reads an input text (file or stdin), tokenizes it into words, punctuation, and whitespace, and replaces each word with a homophonic alternative. It supports:

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

#### Installation  
```bash
git clone https://github.com/scottvr/HoMofo.git
cd HoMofo
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

Experiment with these knobs to craft anything from near-perfect phonetic clones to delightfully absurd puns!

#### License  
MIT © 2025 Your Name  
