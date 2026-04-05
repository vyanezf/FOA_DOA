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
├── nn2.gendsp              # Max/MSP gen~ patch for real-time feature computation
├── features/
│   ├── index.csv           # Master index linking feature CSVs to wav files and labels
│   └── *.csv               # Pre-extracted features (one file per recording)
```

## Audio dataset

The raw wav files (~3 GB) are hosted on Zenodo. Download and unzip into the repo root so the folder structure matches:

```
Files/
  elev0/
  elev40/
  elev75/
  elev0dry/  (or elevzerodry)
```

The wav files are only needed to re-run feature extraction. Training works directly from the pre-extracted `features/` CSVs.

## Setup

```bash
pip install torch numpy pandas scikit-learn soundfile tqdm
```

For the nn~ wrapper:
```bash
pip install nn_tilde
```

## Usage

### Train
```bash
python model.py
# Saves model.pt on best validation performance
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

### Extract features (optional — only if re-running from raw audio)
```bash
python feature_extractor.py metadata/index.csv --out_dir features
```

### Export for nn~ (Max/MSP)
```bash
python wrapper.py -c model.pt -o FOAPred.ts
```
Then load in Max with `[nn~ FOAPred forward]`.

## Inputs and features

The model takes 12 features computed from a 2-second FOA window (W, X, Y, Z channels, 48 kHz, AmbiX format):

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
