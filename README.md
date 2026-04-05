# FOA Direction-of-Arrival Estimator

A multi-layer perceptron (MLP) for estimating sound source direction from First-Order Ambisonics (FOA) recordings. Given 12 acoustic features extracted from a short FOA window, the model jointly predicts azimuth sector, elevation class, and diffuseness class.

## Model outputs

| Head | Classes | Description |
|---|---|---|
| Azimuth | 12 | 30° sectors covering 0–360° |
| Elevation | 3 | Low / mid / high |
| Diffuseness (ψ) | 3 | Low / medium / high |

## Repository structure

```
├── model.py                # MLP architecture, dataset, and training loop
├── feature_extractor.py    # Extracts 12 FOA features from wav files → features/
├── wrapper.py              # Exports the trained model as a TorchScript file for nn~

├── features/
│   ├── index.csv           # Master index linking feature CSVs to wav files and labels
│   └── *.csv               # Pre-extracted features (one file per recording)
├── Max/
│   ├── Example.maxpat      # Example Max patch using nn~ with the exported model
│   ├── FOAPred.ts          # Pre-exported TorchScript model (ready to use)
│   └── nn7.gendsp          # Max/MSP gen~ patch for real-time feature computation
```

## Workflow overview for training on Ambix files

There are three independent use cases. **You do not need to complete all of them** — start from whichever step suits your goal:

```
[Ambix FOA stream] ──(1)──▶ [Feature CSVs] ──(2)──▶ [model.pt] ──(3)──▶ [FOAPred.ts] ──▶ Max/MSP
                          ↑                        ↑                    ↑
                    already in repo          skip if using         already in
                    (step 1 optional)        Max/FOAPred.ts         Max/ folder
```

| Step | What it does | Files needed | Can you skip it? |
|---|---|---|---|
| 1. Feature extraction | Compute features from raw wavs | Raw audio from Zenodo | ✅ Yes — CSVs already in `features/` |
| 2. Training | Train the MLP | `features/` CSVs | ✅ Yes — use the pre-exported `Max/FOAPred.ts` directly |
| 3. Export | Convert `model.pt` → `FOAPred.ts` for Max | `model.pt` (from step 2) | ✅ Yes — `Max/FOAPred.ts` is ready to use |

---

## Setup

Install Python dependencies based on the steps you intend to run:

```bash
# Steps 1 + 2 (feature extraction and/or training)
pip install torch numpy pandas scikit-learn soundfile tqdm

# Step 3 only (export to TorchScript)
pip install torch nn_tilde
```

---

## Step 1 — Feature extraction _(optional)_

Only needed if you want to re-extract features from the raw audio.

Download the raw wav files from Zenodo: https://zenodo.org/records/19432242  
Unzip into the repo root so the structure is:

```
Files/
  elev0/
  elev40/
  elev75/
  elev0dry/
```

Then run:
```bash
python feature_extractor.py metadata/index.csv --out_dir features
```

This overwrites the CSVs in `features/` with freshly computed values.

---

## Step 2 — Training on the Dataset_(optional)_

Trains directly from the pre-extracted feature CSVs — **no audio files needed**.

```bash
python model.py
# Saves model.pt whenever validation performance improves
```

Key arguments:
```
--features_index   Path to features/index.csv  (default: features/index.csv)
--epochs           Number of training epochs    (default: 100)
--lr               Learning rate                (default: 1e-4)
--batch            Batch size                   (default: 128)
--device           auto | cuda | mps | cpu      (default: auto)
--out_ckpt         Output checkpoint path       (default: model.pt)
```

---

## Step 3 — Export for Max/MSP _(optional)_

Only needed if you retrained the model and want to deploy your own `model.pt`.  
**A ready-to-use `FOAPred.ts` is already included in the `Max/` folder.**

```bash
python wrapper.py -c model.pt -o FOAPred.ts
```

Place the resulting `FOAPred.ts` in the nn_tilde models directory:

| OS | Path |
|---|---|
| macOS | `~/Documents/Max 9/Packages/nn_tilde/models/` |
| Windows | `C:\Users\<username>\Documents\Max 9\Packages\nn_tilde\models\` |

---

## Using in Max/MSP

Open `Max/Example.maxpat`. The patch expects a **First-Order Ambisonics stream in AmbiX format** (channel order: W, Y, Z, X) at 48 kHz as input. The `nn7.gendsp` gen~ subpatcher computes the 12 acoustic features in real time and feeds them into `[nn~ FOAPred forward 512]`, which outputs 18 prediction values (12 azimuth + 3 elevation + 3 diffuseness logits).

Load the model with:
```
[nn~ FOAPred forward 512]
```


## Inputs and features

The model takes 12 features computed from a 2-second FOA window (W, Y, Z, X channels, 48 kHz, AmbiX format):

| Feature | Description |
|---|---|
| `mean_x/y/z` | Mean of normalised intensity vector components |
| `std_x/y/z` | Std of normalised intensity vector components |
| `R` | Resultant vector length (directional coherence) |
| `log_rmsW` | Log RMS of the W channel |
| `rX/Y/Z_ratio` | RMS ratio of X/Y/Z to W |
| `psi` | Diffuseness estimate (1 − R) |

## Model architecture

Five fully-connected hidden layers (512 → 512 → 4096 → 4096 → 512) with ReLU activations and dropout, followed by three output heads (azimuth, elevation, diffuseness). Training uses class-balanced cross-entropy with an angular deviation penalty on the azimuth head.
