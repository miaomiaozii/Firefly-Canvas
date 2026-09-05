"""Validate model_specs.json consistency with actual code behavior.

Checks:
1. All models in model_specs.json have valid structure
2. Vision detection matches between JSON and code
3. Aspect ratios in JSON are complete for nano-banana-2

Usage:
    python tools/validate_model_specs.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SPECS_PATH = os.path.join(ROOT, 'app', 'data', 'model_specs.json')


def load_specs():
    """Load model specs from JSON file."""
    with open(SPECS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def validate_structure(specs):
    """Validate that all model entries have valid structure."""
    errors = []
    
    required_fields = ['display_name', 'api_type', 'supports_vision']
    categories = ['chat_models', 'image_models', 'video_models']
    
    for category in categories:
        models = specs.get(category, {})
        if not models:
            errors.append(f"WARNING: {category} is empty")
            continue
        
        for model_id, spec in models.items():
            # Check required fields
            for field in required_fields:
                if field not in spec:
                    errors.append(f"{category}.{model_id}: missing field '{field}'")
            
            # Check parameters structure
            params = spec.get('parameters', {})
            for param_name, param_spec in params.items():
                if not isinstance(param_spec, dict):
                    errors.append(f"{category}.{model_id}.{param_name}: must be dict")
                elif 'type' not in param_spec:
                    errors.append(f"{category}.{model_id}.{param_name}: missing 'type'")
    
    return errors


def validate_vision_detection(specs):
    """Validate vision detection matches between JSON and code."""
    errors = []
    
    # Import the function
    from app.core import looks_like_vision_chat_model
    
    # Check all chat models
    for model_id, spec in specs.get('chat_models', {}).items():
        json_vision = spec.get('supports_vision', False)
        code_vision = looks_like_vision_chat_model(model_id)
        
        if json_vision != code_vision:
            errors.append(
                f"MISMATCH: {model_id} - JSON says vision={json_vision}, "
                f"code says vision={code_vision}"
            )
    
    return errors


def validate_aspect_ratios(specs):
    """Validate aspect ratios are complete for key models."""
    errors = []
    
    # nano-banana-2 should have 14 aspect ratios (per provider docs)
    nano_banana_2 = specs.get('image_models', {}).get('nano-banana-2')
    if nano_banana_2:
        ratios = nano_banana_2.get('parameters', {}).get('aspect_ratio', {}).get('options', [])
        expected_ratios = ['4:3', '3:4', '16:9', '9:16', '2:3', '3:2', '1:1', '4:5', '5:4', '21:9', '1:4', '4:1', '8:1', '1:8']
        
        if len(ratios) != 14:
            errors.append(f"nano-banana-2: expected 14 aspect ratios, got {len(ratios)}")
        
        missing = [r for r in expected_ratios if r not in ratios]
        if missing:
            errors.append(f"nano-banana-2: missing aspect ratios: {missing}")
    
    # seedance video models should have 3 aspect ratios (1:1, 16:9, 9:16)
    for model_id in ['seedance-2.0-pro', 'seedance-2.0-fast', 'seedance-1.5-pro', 'seedance-1.0-pro', 'seedance-1.0-pro-fast']:
        seedance = specs.get('video_models', {}).get(model_id)
        if seedance:
            ratios = seedance.get('parameters', {}).get('aspect_ratio', {}).get('options', [])
            expected_ratios = ['1:1', '16:9', '9:16']
            if len(ratios) != 3:
                errors.append(f"{model_id}: expected 3 aspect ratios, got {len(ratios)}")
            missing = [r for r in expected_ratios if r not in ratios]
            if missing:
                errors.append(f"{model_id}: missing aspect ratios: {missing}")
    
    return errors


def main():
    """Run all validation checks."""
    print("Validating model_specs.json...")
    print("=" * 60)
    
    specs = load_specs()
    
    all_errors = []
    
    # Run validations
    print("\n[1/3] Checking structure...")
    errors = validate_structure(specs)
    if errors:
        for e in errors:
            print(f"  ERROR: {e}")
        all_errors.extend(errors)
    else:
        print("  OK: Structure valid")
    
    print("\n[2/3] Checking vision detection consistency...")
    errors = validate_vision_detection(specs)
    if errors:
        for e in errors:
            print(f"  ERROR: {e}")
        all_errors.extend(errors)
    else:
        print("  OK: Vision detection consistent")
    
    print("\n[3/3] Checking aspect ratios...")
    errors = validate_aspect_ratios(specs)
    if errors:
        for e in errors:
            print(f"  ERROR: {e}")
        all_errors.extend(errors)
    else:
        print("  OK: Aspect ratios complete")
    
    # Summary
    print("\n" + "=" * 60)
    if all_errors:
        print(f"ERROR: Found {len(all_errors)} error(s)")
        return 1
    else:
        print("OK: All validations passed")
        return 0


if __name__ == '__main__':
    sys.exit(main())
