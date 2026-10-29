"""Test the model_specs service module functionality."""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.services.model_specs import (
    load_specs,
    get_model_spec,
    is_vision_model,
    get_api_type,
    get_aspect_ratios,
    get_image_sizes,
    get_model_list_by_category,
)


def test_load_specs():
    """Test loading specs from JSON."""
    specs = load_specs()
    assert specs, "Specs should not be empty"
    assert 'chat_models' in specs, "Should have chat_models"
    assert 'image_models' in specs, "Should have image_models"
    assert 'video_models' in specs, "Should have video_models"
    print("OK: load_specs()")


def test_get_model_spec():
    """Test getting individual model specs."""
    # Test exact match
    spec = get_model_spec("nano-banana-2")
    assert spec is not None, "Should find nano-banana-2"
    assert spec['api_type'] == 'images_generations', "Wrong API type"
    print("OK: get_model_spec('nano-banana-2')")
    
    # Test partial match
    spec = get_model_spec("gpt-4")
    assert spec is not None, "Should find GPT-4 model"
    print("OK: get_model_spec('gpt-4') partial match")
    
    # Test non-existent model
    spec = get_model_spec("non-existent-model")
    assert spec is None, "Should return None for non-existent model"
    print("OK: get_model_spec('non-existent-model') returns None")


def test_is_vision_model():
    """Test vision model detection (contract: JSON supports_vision flag)."""
    # Should be True for models the spec marks as vision-capable
    assert is_vision_model("gpt-4o") == True, "gpt-4o should support vision"
    assert is_vision_model("nano-banana-2") == True, "nano-banana-2 should support vision"
    assert is_vision_model("doubao-seedance-2-0-260128") == True, "seedance2.0 统一格式 ID should support vision"
    assert is_vision_model("gpt-image-2") == True, "gpt-image-2 supports image editing (JSON declares 'image' param)"
    print("OK: is_vision_model() for supported models")
    
    # Should be False for models not in the spec (legacy name heuristics only)
    assert is_vision_model("non-existent-model") == False, "unknown model should fall back to name detection"
    print("OK: is_vision_model() for non-vision models")


def test_get_api_type():
    """Test getting API type."""
    assert get_api_type("gpt-4o") == "responses", "gpt-4o should use responses API"
    assert get_api_type("nano-banana-2") == "images_generations", "nano-banana-2 should use images_generations API"
    assert get_api_type("veo3-fast") == "v2_generations", "veo3-fast should use v2_generations API"
    print("OK: get_api_type()")


def test_get_aspect_ratios():
    """Test getting aspect ratios."""
    ratios = get_aspect_ratios("nano-banana-2")
    assert len(ratios) == 15, f"nano-banana-2 should have 15 aspect ratios (full Gemini image set incl. 9:21 mirror), got {len(ratios)}"
    assert "1:1" in ratios, "Should have 1:1 ratio"
    assert "4:3" in ratios, "Should have 4:3 ratio"
    assert "9:21" in ratios, "Should have the 21:9 mirror ratio 9:21"
    print("OK: get_aspect_ratios('nano-banana-2')")
    
    # Test model without aspect ratios
    ratios = get_aspect_ratios("veo3-fast")
    assert ratios == [], "veo3-fast should not have aspect ratios"
    print("OK: get_aspect_ratios('veo3-fast') returns empty")


def test_get_image_sizes():
    """Test getting image sizes."""
    sizes = get_image_sizes("nano-banana-2")
    assert len(sizes) == 3, f"nano-banana-2 should have 3 image sizes, got {len(sizes)}"
    assert "1K" in sizes, "Should have 1K size"
    assert "4K" in sizes, "Should have 4K size"
    print("OK: get_image_sizes('nano-banana-2')")


def test_get_model_list_by_category():
    """Test getting model list by category."""
    chat_models = get_model_list_by_category("chat_models")
    assert len(chat_models) > 0, "Should have chat models"
    assert "gpt-4o-mini" in chat_models, "Should have gpt-4o-mini"
    print("OK: get_model_list_by_category('chat_models')")
    
    image_models = get_model_list_by_category("image_models")
    assert len(image_models) > 0, "Should have image models"
    print("OK: get_model_list_by_category('image_models')")
    
    video_models = get_model_list_by_category("video_models")
    assert len(video_models) > 0, "Should have video models"
    print("OK: get_model_list_by_category('video_models')")


def main():
    """Run all tests."""
    print("Testing model_specs service module...")
    print("=" * 50)
    
    test_load_specs()
    test_get_model_spec()
    test_is_vision_model()
    test_get_api_type()
    test_get_aspect_ratios()
    test_get_image_sizes()
    test_get_model_list_by_category()
    
    print("=" * 50)
    print("OK: All tests passed!")


if __name__ == '__main__':
    main()
