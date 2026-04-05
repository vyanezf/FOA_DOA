#!/usr/bin/env python3
"""
wrapper.py

nn_tilde wrapper for the FOA direction-of-arrival MLP model.
"""
import torch
import numpy as np
from typing import List
from model import ThreeHiddenMLP

# You'll need to install nn_tilde first: pip install nn_tilde
try:
    import nn_tilde
except ImportError:
    print("Error: nn_tilde not installed. Install with: pip install nn_tilde")
    exit(1)

class MLPWrapperFresh(nn_tilde.Module):
    def __init__(self, checkpoint_path: str = "model.pt"):
        super().__init__()
        
        # Load the trained model
        self.model = ThreeHiddenMLP()
        if checkpoint_path:
            sd = torch.load(checkpoint_path, map_location='cpu')
            self.model.load_state_dict(sd)
        self.model.eval()  # Set to eval mode once during initialization
        
        # Register method for nn~
        self.register_method(
            'forward',
            in_channels=12,      # 12 input channels (one per feature)
            in_ratio=1,         # 1 sample per channel = 1 feature value
            out_channels=18,    # 18 output values (12 az + 3 el + 3 psi)
            out_ratio=1,        # output 1 value per channel
            input_labels=[
                '(signal) mean_x', '(signal) mean_y', '(signal) mean_z',
                '(signal) std_x', '(signal) std_y', '(signal) std_z',
                '(signal) R', '(signal) log_rmsW', '(signal) rX_ratio',
                '(signal) rY_ratio', '(signal) rZ_ratio', '(signal) psi'
            ],
            output_labels=[
                '(signal) az_0', '(signal) az_1', '(signal) az_2', '(signal) az_3', 
                '(signal) az_4', '(signal) az_5', '(signal) az_6', '(signal) az_7',
                '(signal) az_8', '(signal) az_9', '(signal) az_10', '(signal) az_11',
                '(signal) el_0', '(signal) el_1', '(signal) el_2',
                '(signal) psi_0', '(signal) psi_1', '(signal) psi_2'
            ]
        )
        
        # Register some optional attributes
        self.register_attribute('model_loaded', True)
        self.register_attribute('checkpoint_path', checkpoint_path)

    @torch.jit.export
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Main inference method for nn~
        x: [B, 12, N] where N is the buffer size from nn~
        returns: [B, 18, N] predictions broadcast across buffer
        """
        batch_size = x.shape[0]
        buffer_size = x.shape[2] if x.dim() == 3 else 1
        
        # If input has 3 dimensions [B, 12, N], take mean across the buffer dimension
        if x.dim() == 3:
            # Average across the buffer dimension to get [B, 12] features
            features = x.mean(dim=2)
        else:
            features = x
        
        # Run inference (model should already be in eval mode)
        with torch.no_grad():
            output = self.model(features)
            # Concatenate outputs: azimuth (12) + elevation (3) + psi (3) = 18
            result = torch.cat([output['az'], output['el'], output['psi']], dim=1)
            
            # Only add buffer dimension if input had 3D (for nn_tilde compatibility)
            if x.dim() == 3:
                result = result.unsqueeze(2).expand(-1, -1, buffer_size)
            
            return result

    # Attribute getters (required by nn_tilde)
    @torch.jit.export
    def get_model_loaded(self) -> bool:
        return self.model_loaded[0]
    
    @torch.jit.export
    def get_checkpoint_path(self) -> str:
        return self.checkpoint_path[0]

    # Attribute setters (optional but good practice)
    @torch.jit.export
    def set_model_loaded(self, value: bool) -> int:
        self.model_loaded = (value,)
        return 0

def main():
    """Export the TorchScript model for nn~"""
    import argparse
    
    p = argparse.ArgumentParser(description="Export fresh MLP model for nn_tilde")
    p.add_argument("-c", "--checkpoint", default="model.pt",
                   help="Path to MLP checkpoint")
    p.add_argument("-o", "--output", default="FOAPred.ts",
                   help="Output TorchScript file for nn~")
    args = p.parse_args()
    
    print(f"Loading model from: {args.checkpoint}")
    wrapper = MLPWrapperFresh(args.checkpoint)
    
    # Test the wrapper
    print("Testing wrapper...")
    test_input = torch.randn(1, 12)  # Batch of 1, 12 features
    output = wrapper.forward(test_input)
    print(f"✅ Test successful! Input shape: {test_input.shape}, Output shape: {output.shape}")
    
    # Export to TorchScript for nn~
    print(f"Exporting to: {args.output}")
    wrapper.export_to_ts(args.output)
    print(f"✅ Successfully exported to {args.output}")
    print("\nYou can now use this .ts file with the nn~ object in Max/MSP!")
    print("Example usage in Max: [nn~ FOAPred forward]")

if __name__ == "__main__":
    main()