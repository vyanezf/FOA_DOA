#!/usr/bin/env python3
"""
feature_extractor.py

For each unique wav in metadata/index.csv:
  - collect all rows (time_s, labels) for that file
  - window each snippet, compute 12 FOA features + labels
  - write a single CSV: features/<wavbasename>.csv
      header: mean_x, ..., psi, azimuth_deg, az_sector, elev_class, psi_cls
      one row per time_s in chronological order
"""
import argparse
import numpy as np
import pandas as pd
import soundfile as sf
from pathlib import Path
from tqdm import tqdm

SR      = 48000
WIN_SEC = 2.0
N       = int(SR * WIN_SEC)

FEATURE_NAMES = [
    "mean_x","mean_y","mean_z",
    "std_x","std_y","std_z",
    "R",
    "log_rmsW","rX_ratio","rY_ratio","rZ_ratio",
    "psi"
]
LABEL_NAMES = ["time_s","azimuth_deg","az_sector","elev_class","psi_cls"]
ALL_NAMES   = FEATURE_NAMES + LABEL_NAMES

def reorder_ambix(block: np.ndarray):
    """W,Y,Z,X → W,X,Y,Z"""
    W = block[:,0]; Y = block[:,1]
    Z = block[:,2]; X = block[:,3]
    return np.stack([W,X,Y,Z], axis=1)

def compute_features(window: np.ndarray):
    W,X,Y,Z = window.T
    # instants
    Ix = W*X; Iy = W*Y; Iz = W*Z
    Imag = np.sqrt(Ix*Ix + Iy*Iy + Iz*Iz) + 1e-12
    ux,uy,uz = Ix/Imag, Iy/Imag, Iz/Imag

    # stats
    mean_x,mean_y,mean_z = ux.mean(), uy.mean(), uz.mean()
    std_x,std_y,std_z    = ux.std(),  uy.std(),  uz.std()
    R = np.sqrt(mean_x**2 + mean_y**2 + mean_z**2)

    # rms
    rmsW = np.sqrt((W*W).mean()) + 1e-12
    rmsX = np.sqrt((X*X).mean()) + 1e-12
    rmsY = np.sqrt((Y*Y).mean()) + 1e-12
    rmsZ = np.sqrt((Z*Z).mean()) + 1e-12

    log_rmsW = np.log(rmsW + 1.0)
    rX_ratio = rmsX / rmsW
    rY_ratio = rmsY / rmsW
    rZ_ratio = rmsZ / rmsW

    # diffuseness - match Gen code exactly: psi = 1 - R
    psi = float(np.clip(1.0 - R, 0, 1))

    return np.array([
        mean_x,mean_y,mean_z,
        std_x,std_y,std_z,
        R,
        log_rmsW,rX_ratio,rY_ratio,rZ_ratio,
        psi
    ], dtype=np.float32)

def psi_to_class(val: float) -> int:
    if val <= 0.15: return 0
    if val <= 0.3:  return 1
    if val <= 0.45: return 2
    return 3

def main():
    p = argparse.ArgumentParser(
        description="Grouped FOA feature extraction → one CSV per wav")
    p.add_argument("metadata_csv", type=Path, nargs="?", default=Path("metadata/index.csv"),
                   help="metadata/index.csv with columns: wav_path,time_s,"
                        "azimuth_deg,az_sector,elev_class,psi_overall (default: metadata/index.csv)")
    p.add_argument("--out_dir", type=Path, default=Path("features"),
                   help="where to write grouped CSVs")
    args = p.parse_args()

    if not args.metadata_csv.is_file():
        p.error(f"Metadata CSV not found: {args.metadata_csv}")
    df = pd.read_csv(args.metadata_csv)
    args.out_dir.mkdir(exist_ok=True, parents=True)

    # ensure labels exist
    if "psi_overall" not in df.columns:
        p.error("metadata/index.csv must include a psi_overall column")

    for wav_path, group in tqdm(df.groupby("wav_path"), desc="Files"):
        wav = Path(wav_path)
        data, sr = sf.read(str(wav), always_2d=True)
        if sr != SR:
            raise ValueError(f"{wav}: expected {SR} Hz, got {sr}")

        # prepare output rows
        rows = []
        # sort by time_s
        group = group.sort_values("time_s")
        for _, row in group.iterrows():
            t0 = float(row.time_s)
            start = int(round(t0*SR))
            end   = start + N
            if data.shape[0] >= end:
                block = data[start:end]
            else:
                # pad
                block = np.zeros((N, data.shape[1]), np.float32)
                avail = data[start:]
                block[:len(avail)] = avail

            foa4 = reorder_ambix(block[:,:4])
            feats = compute_features(foa4)

            # labels
            az_deg    = float(row.azimuth_deg)
            # Use the SAME azimuth fix as in the working training: (azimuth_deg + 360) % 360 // 30
            az_deg_fixed = (az_deg + 360) % 360
            az_sector = int(az_deg_fixed // 30)
            el_cls    = int(row.elev_class)
            psi_cls   = int(row.psi_cls) if hasattr(row, 'psi_cls') else psi_to_class(float(row.psi_overall))

            # features + timestamp + labels
            rows.append(np.concatenate([
                feats,
                [t0, az_deg, az_sector, el_cls, psi_cls]
            ]))

        # assemble DataFrame and write
        out_df = pd.DataFrame(rows, columns=ALL_NAMES)
        out_file = args.out_dir / f"{wav.stem}.csv"
        out_df.to_csv(out_file, index=False, float_format="%.6f")

    print(f"✅ wrote {len(df['wav_path'].unique())} CSVs → {args.out_dir}")

if __name__=="__main__":
    main()