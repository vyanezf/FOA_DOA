#!/usr/bin/env python3
"""
model.py

FOA direction-of-arrival MLP: architecture, dataset, and training loop.
"""
import argparse
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from pathlib import Path
import torch.nn.functional as F
from pathlib import Path
import torch.optim as optim
import torch.optim.lr_scheduler as lr_scheduler
from sklearn.model_selection import train_test_split
from sklearn.utils.class_weight import compute_class_weight
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm

AZ_LOSS_WEIGHT = 1  # multiply azimuth head loss for stronger emphasis
ANG_PEN_WEIGHT = 1.2  # weight for angular deviation penalty

# — network architecture settings (update only here)
HIDDEN_DIMS = [512, 512, 4096, 4096, 512]  # sizes for fc1→fc2→fc3→fc4→fc5
DROPOUT = 0.01          # dropout probability

# ─── model ────────────────────────────────────────────────────────────────
class ThreeHiddenMLP(nn.Module):
    def __init__(self, in_dim=12, hidden_dims=HIDDEN_DIMS, dropout=DROPOUT):
        super().__init__()
        # unpack hidden layer sizes for five layers
        h1, h2, h3, h4, h5 = hidden_dims
        self.fc1 = nn.Linear(in_dim,  h1)
        self.fc2 = nn.Linear(h1,      h2)
        self.fc3 = nn.Linear(h2,      h3)
        self.fc4 = nn.Linear(h3,      h4)
        self.fc5 = nn.Linear(h4,      h5)
        self.dropout = nn.Dropout(dropout)
        # heads on final layer output
        self.az  = nn.Linear(h5, 12)
        self.el  = nn.Linear(h5, 3)
        self.psi = nn.Linear(h5, 3)

    def forward(self, x):
        x = F.relu(self.fc1(x)); x = self.dropout(x)
        x = F.relu(self.fc2(x)); x = self.dropout(x)
        x = F.relu(self.fc3(x)); x = self.dropout(x)
        x = F.relu(self.fc4(x)); x = self.dropout(x)
        x = F.relu(self.fc5(x)); x = self.dropout(x)
        return {'az': self.az(x), 'el': self.el(x), 'psi': self.psi(x)}

# ─── dataset ────────────────────────────────────────────────────────────────────
class CSVDataset(Dataset):
    def __init__(self, index_path: Path):
        """Load features from consolidated index CSV"""
        df = pd.read_csv(index_path)
        self.X = []
        self.y_az = []
        self.y_el = []
        self.y_psi = []
        
        for r in df.itertuples():
            # load grouped per-wav CSV for this snippet
            df_feat = pd.read_csv(r.feature_csv)
            # find the row matching time_s
            match = df_feat[np.isclose(df_feat['time_s'], r.time_s)]
            if match.shape[0] != 1:
                raise ValueError(
                    f"Feature file {r.feature_csv} time_s={r.time_s} matches {match.shape[0]} rows"
                )
            # extract the 12 feature columns
            feats = match.iloc[0, :12].to_numpy(dtype=np.float32)
            self.X.append(feats)
            # labels
            # Fix azimuth: convert [-180,180] to [0,360] then to class
            az_deg_fixed = (r.azimuth_deg + 360) % 360
            self.y_az.append(int(az_deg_fixed // 30))
            self.y_el.append(int(r.elev_class))
            # Use the balanced psi_cls from index instead of computing from psi_overall
            self.y_psi.append(int(r.psi_cls))

        # just stack features; no normalization
        self.X = np.stack(self.X, 0)
        self.y_az = np.array(self.y_az)
        self.y_el = np.array(self.y_el)
        self.y_psi = np.array(self.y_psi)
        
        print(f"Loaded {len(self.X)} samples")
        print(f"Azimuth class distribution: {np.bincount(self.y_az)}")
        print(f"Elevation class distribution: {np.bincount(self.y_el)}")
        print(f"Psi class distribution: {np.bincount(self.y_psi)}")

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return (torch.from_numpy(self.X[idx]), self.y_az[idx], self.y_el[idx], self.y_psi[idx])

# ─── training ────────────────────────────────────────────────────────────────
def train_epoch(model, loader, opt, device, el_weights=None, psi_weights=None):
    model.train()
    total_loss = 0
    for x, az_t, el_t, psi_t in tqdm(loader, desc="Train", leave=False):
        x = x.to(device)
        az_t = az_t.to(device)
        el_t = el_t.to(device)
        psi_t= psi_t.to(device)
        out = model(x)
        # weight azimuth loss to emphasize az accuracy with angular deviation penalty
        loss_az = F.cross_entropy(out['az'], az_t)
        probs = F.softmax(out['az'], dim=1)
        class_idx = torch.arange(out['az'].size(1), device=device).float().unsqueeze(0)
        loss_ang = torch.mean(torch.abs(class_idx - az_t.unsqueeze(1).float()) * probs)
        
        # Use weighted loss for elevation and psi to handle class imbalance
        if el_weights is not None:
            loss_el = F.cross_entropy(out['el'], el_t, weight=el_weights)
        else:
            loss_el = F.cross_entropy(out['el'], el_t)
            
        if psi_weights is not None:
            loss_psi = F.cross_entropy(out['psi'], psi_t, weight=psi_weights)
        else:
            loss_psi = F.cross_entropy(out['psi'], psi_t)
            
        loss = AZ_LOSS_WEIGHT * loss_az + ANG_PEN_WEIGHT * loss_ang + loss_el + loss_psi
        opt.zero_grad(); loss.backward(); opt.step()
        total_loss += loss.item()*x.size(0)
    return total_loss/len(loader.dataset)

def eval_all(model, loader, device):
    """Evaluate az, el, psi accuracy over the loader."""
    model.eval()
    az_correct, el_correct, psi_correct = 0, 0, 0
    total = 0
    with torch.no_grad():
        for x, az_t, el_t, psi_t in tqdm(loader, desc="Eval", leave=False):
            x = x.to(device)
            az_t = az_t.to(device)
            el_t = el_t.to(device)
            psi_t = psi_t.to(device)
            out = model(x)
            az_pred = out['az'].argmax(1)
            el_pred = out['el'].argmax(1)
            psi_pred = out['psi'].argmax(1)
            az_correct += (az_pred == az_t).sum().item()
            el_correct += (el_pred == el_t).sum().item()
            psi_correct += (psi_pred == psi_t).sum().item()
            total += x.size(0)
    return {
        'az': az_correct/total,
        'el': el_correct/total,
        'psi': psi_correct/total
    }

# ─── main ────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser()
    # 5-layer network architecture: 512 → 512 → 4096 → 4096 → 512, dropout=0.2
    p.add_argument("--features_index", "-f",
                   default="features/index.csv",
                   help="Path to consolidated features/index.csv (default: features/index.csv)")
    p.add_argument("--batch",     type=int, default=128)
    p.add_argument("--epochs",    type=int, default=100)  # fewer epochs for fine-tuning
    p.add_argument("--lr",        type=float, default=1e-4)  # lower LR for fine-tuning
    p.add_argument("--device",    default="auto",
                   help="Device to train on: 'auto', 'cuda', 'mps', or 'cpu' (default: auto)")
    p.add_argument("--out_ckpt",  default="model.pt")
    p.add_argument("--resume",    "-r", default="model_prev.pt",
                   help="Path to model checkpoint to resume training from (default: model_prev.pt)")
    args = p.parse_args()

    # auto-select device
    if args.device == 'auto':
        if torch.cuda.is_available():
            dev_name = 'cuda'
        elif getattr(torch.backends, 'mps', None) and torch.backends.mps.is_available():
            dev_name = 'mps'
        else:
            dev_name = 'cpu'
    else:
        dev_name = args.device
    dev = torch.device(dev_name)

    ds = CSVDataset(Path(args.features_index))
    tr_idx, va_idx = train_test_split(
        range(len(ds)), test_size=0.2, random_state=42
    )
    tr_ds = torch.utils.data.Subset(ds, tr_idx)
    va_ds = torch.utils.data.Subset(ds, va_idx)

    tr_ld = DataLoader(tr_ds, batch_size=args.batch, shuffle=True)
    va_ld = DataLoader(va_ds, batch_size=args.batch, shuffle=False)

    # build model, optimizer, scheduler
    model = ThreeHiddenMLP().to(dev)
    opt = optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = lr_scheduler.ReduceLROnPlateau(opt, mode='max', patience=5, factor=0.5)

    # Compute class weights for balanced training (preserve azimuth, fix elevation)
    print("Computing class weights for balanced training...")
    all_y_el = []
    all_y_psi = []
    for _, (_, y_az, y_el, y_psi) in enumerate(tr_ld):
        all_y_el.extend(y_el.cpu().numpy())
        all_y_psi.extend(y_psi.cpu().numpy())
    
    # Compute balanced weights for elevation and psi (keep azimuth unweighted)
    el_classes = np.unique(all_y_el)
    psi_classes = np.unique(all_y_psi)
    
    el_weights = compute_class_weight('balanced', classes=el_classes, y=all_y_el)
    psi_weights = compute_class_weight('balanced', classes=psi_classes, y=all_y_psi)
    
    # Ensure weights cover all possible classes (pad with 1.0 for missing classes)
    el_weight_full = np.ones(3)  # 3 elevation classes
    psi_weight_full = np.ones(3)  # 3 psi classes
    
    for i, cls in enumerate(el_classes):
        el_weight_full[cls] = el_weights[i]
    for i, cls in enumerate(psi_classes):
        psi_weight_full[cls] = psi_weights[i]
    
    el_weight_tensor = torch.FloatTensor(el_weight_full).to(dev)
    psi_weight_tensor = torch.FloatTensor(psi_weight_full).to(dev)
    
    print(f"Elevation class weights: {dict(zip(range(3), el_weight_full))}")
    print(f"Psi class weights: {dict(zip(range(3), psi_weight_full))}")

    # Load checkpoint to preserve azimuth progress
    start_epoch = 1
    best_acc = 0.0
    initial_metrics = None
    
    # Fresh training - no checkpoint loading
    print("Starting fresh training from randomly initialized weights...")
    
    # evaluate initial random model to set the baseline
    initial_metrics = eval_all(model, va_ld, dev)
    best_acc = initial_metrics['az']
    print(f"✅ Current performance - az@1={initial_metrics['az']:.3f}, el@1={initial_metrics['el']:.3f}, psi@1={initial_metrics['psi']:.3f}")

    # Track best scores for selective saving
    best_combined_score = 0.6 * initial_metrics['az'] + 0.2 * initial_metrics['el'] + 0.2 * initial_metrics['psi'] if initial_metrics else 0.0
    best_azimuth = initial_metrics['az'] if initial_metrics else 0.0
    best_model_state = model.state_dict().copy() if initial_metrics else None  # Start with current model as best

    for ep in range(start_epoch, args.epochs+1):
        loss = train_epoch(model, tr_ld, opt, dev, el_weight_tensor, psi_weight_tensor)
        metrics = eval_all(model, va_ld, dev)
        print(f"Ep{ep:02d}  loss={loss:.4f}  az@1={metrics['az']:.3f}  el@1={metrics['el']:.3f}  psi@1={metrics['psi']:.3f}")
        
        # Calculate combined score (weighted toward azimuth preservation)
        combined_score = 0.6 * metrics['az'] + 0.2 * metrics['el'] + 0.2 * metrics['psi']
        
        # Smart checkpoint saving: allow beneficial trade-offs
        should_save = False
        save_reason = ""
        
        if metrics['az'] > best_azimuth:
            # Always save if azimuth improves
            should_save = True
            save_reason = f"azimuth improved to {metrics['az']:.3f}"
            best_azimuth = metrics['az']
            best_model_state = model.state_dict().copy()
            best_combined_score = max(best_combined_score, combined_score)
        else:
            # Calculate the trade-off: is the gain in other metrics worth the azimuth loss?
            az_loss = best_azimuth - metrics['az']
            el_gain = max(0, metrics['el'] - initial_metrics['el'])
            psi_gain = max(0, metrics['psi'] - initial_metrics['psi'])
            
            # Net benefit calculation: weight azimuth losses vs other gains
            # Allow azimuth drops if the gains in other metrics are significantly larger
            net_benefit = (el_gain * 2) + (psi_gain * 2) - (az_loss * 1)  # 2:1 ratio favoring improvements
            
            if net_benefit > 0 and az_loss <= 0.1:  # Max 10% azimuth drop for beneficial trades
                should_save = True
                save_reason = f"beneficial trade-off (az-{az_loss:.3f}, el+{el_gain:.3f}, psi+{psi_gain:.3f}, net+{net_benefit:.3f})"
                best_model_state = model.state_dict().copy()
                best_combined_score = max(best_combined_score, combined_score)
        
        if should_save:
            torch.save(best_model_state, args.out_ckpt)
            print(f"✅ Checkpoint saved: {args.out_ckpt} at epoch {ep} ({save_reason})")
        
        
        # step scheduler based on azimuth (preserve azimuth progress)
        scheduler.step(metrics['az'])
        current_lr = opt.param_groups[0]['lr']
        tqdm.write(f"LR={current_lr:.3e}")

    # Final evaluation and save
    final_metrics = eval_all(model, va_ld, dev)
    
    # Check if final epoch has better azimuth than our saved best
    if final_metrics['az'] > best_azimuth:
        best_model_state = model.state_dict().copy()
        best_azimuth = final_metrics['az']
        torch.save(best_model_state, args.out_ckpt)
        print(f"✅ Final epoch achieved best azimuth! Saved to: {args.out_ckpt}")
    elif (final_metrics['az'] >= best_azimuth * 0.995 and 
          (final_metrics['el'] > initial_metrics['el'] * 1.1 or final_metrics['psi'] > initial_metrics['psi'] * 1.2)):
        best_model_state = model.state_dict().copy()
        torch.save(best_model_state, args.out_ckpt)
        print(f"✅ Final epoch has significant other improvements! Saved to: {args.out_ckpt}")
    else:
        # Keep the best azimuth model we found during training
        print(f"ℹ️  Final epoch not better than best. Best azimuth model saved to: {args.out_ckpt}")
    
    print(f"✅ Done — Final performance: az@1={final_metrics['az']:.3f}, el@1={final_metrics['el']:.3f}, psi@1={final_metrics['psi']:.3f}")
    print(f"Best azimuth achieved: {best_azimuth:.3f}")
    print(f"Model in {args.out_ckpt} contains: Best azimuth model from training")

if __name__=="__main__":
    main()
