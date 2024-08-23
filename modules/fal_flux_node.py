import os
import fal_client
import folder_paths
import configparser
import base64
import io
from PIL import Image
import logging
import json
import requests
import numpy as np
import torch

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class FalAPIFluxNode:
    def __init__(self):
        self.api_key = self.get_api_key()
        os.environ['FAL_KEY'] = self.api_key

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True}),
                "image_size": (["square_hd", "square", "portrait_4_3", "portrait_16_9", "landscape_4_3", "landscape_16_9"],),
                "num_inference_steps": ("INT", {"default": 28, "min": 1, "max": 100}),
                "guidance_scale": ("FLOAT", {"default": 3.5, "min": 0.1, "max": 20.0}),
                "num_images": ("INT", {"default": 1, "min": 1, "max": 4}),
            },
            "optional": {
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "lora_1": ("LORA_CONFIG",),
                "lora_2": ("LORA_CONFIG",),
                "lora_3": ("LORA_CONFIG",),
                "lora_4": ("LORA_CONFIG",),
                "lora_5": ("LORA_CONFIG",),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "generate"
    CATEGORY = "image generation"

    def get_api_key(self):
        config = configparser.ConfigParser()
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.ini')
        if os.path.exists(config_path):
            config.read(config_path)
            return config.get('falai', 'api_key', fallback=None)
        return None

    def generate(self, prompt, image_size, num_inference_steps, guidance_scale, num_images, seed=None, **kwargs):
        if not self.api_key:
            raise ValueError("API key is not set. Please check your config.ini file.")

        arguments = {
            "prompt": prompt,
            "image_size": image_size,
            "num_inference_steps": num_inference_steps,
            "guidance_scale": guidance_scale,
            "num_images": num_images,
            "enable_safety_checker": True
        }

        if seed is not None and seed != 0:
            arguments["seed"] = seed

        lora_list = []
        for i in range(1, 6):
            lora_config = kwargs.get(f"lora_{i}")
            if lora_config:
                lora_list.append(lora_config)
        
        if lora_list:
            arguments["loras"] = lora_list

        logger.debug(f"Full API request payload: {json.dumps(arguments, indent=2)}")

        try:
            handler = fal_client.submit(
                "fal-ai/flux-lora",
                arguments=arguments,
            )
            result = handler.get()
            logger.debug(f"API response: {json.dumps(result, indent=2)}")
        except Exception as e:
            logger.error(f"API error details: {str(e)}")
            if hasattr(e, 'response'):
                logger.error(f"API error response: {e.response.text}")
            raise RuntimeError(f"An error occurred when calling the fal.ai API: {str(e)}") from e

        if "images" not in result or not result["images"]:
            logger.error("No images were generated by the API.")
            raise RuntimeError("No images were generated by the API.")

        output_images = []
        for index, img_info in enumerate(result["images"]):
            try:
                logger.debug(f"Processing image {index}: {json.dumps(img_info, indent=2)}")
                if not isinstance(img_info, dict) or "url" not in img_info or not img_info["url"]:
                    logger.error(f"Invalid image info for image {index}")
                    continue
                
                img_url = img_info["url"]
                logger.debug(f"Image URL: {img_url[:100]}...")  # Log the first 100 characters of the URL

                if img_url.startswith("data:image"):
                    # Handle Base64 encoded image
                    try:
                        _, img_data = img_url.split(",", 1)
                        img_data = base64.b64decode(img_data)
                    except ValueError:
                        logger.error(f"Failed to split image URL for image {index}")
                        continue
                else:
                    # Handle regular URL
                    try:
                        response = requests.get(img_url)
                        response.raise_for_status()
                        img_data = response.content
                    except requests.RequestException as e:
                        logger.error(f"Failed to download image from URL for image {index}: {str(e)}")
                        continue

                # Log the first few bytes of the image data
                logger.debug(f"First 20 bytes of image data: {img_data[:20]}")

                # Try to interpret the data as an image
                try:
                    img = Image.open(io.BytesIO(img_data))
                    logger.debug(f"Opened image with size: {img.size} and mode: {img.mode}")
                except Exception as e:
                    logger.error(f"Failed to open image data: {str(e)}")
                    # If opening as an image fails, try to interpret it as raw pixel data
                    img_np = np.frombuffer(img_data, dtype=np.uint8)
                    logger.debug(f"Interpreted as raw pixel data with shape: {img_np.shape}")
                    
                    # If the shape is (1024,), reshape it to a more sensible image size
                    if img_np.shape == (1024,):
                        img_np = img_np.reshape(32, 32)  # Reshape to 32x32 image
                    elif img_np.shape == (1, 1, 1024):
                        img_np = img_np.reshape(32, 32)
                    
                    # Normalize the data to 0-255 range
                    img_np = ((img_np - img_np.min()) / (img_np.max() - img_np.min()) * 255).astype(np.uint8)
                    
                    img = Image.fromarray(img_np, 'L')  # Create grayscale image
                    img = img.convert('RGB')  # Convert to RGB
                
                # Ensure image is in RGB mode
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Convert PIL Image to NumPy array
                img_np = np.array(img).astype(np.float32) / 255.0
                
                # Convert NumPy array to PyTorch tensor
                img_tensor = torch.from_numpy(img_np)
                
                output_images.append(img_tensor)
            except Exception as e:
                logger.error(f"Failed to process image {index}: {str(e)}")

        if not output_images:
            logger.error("Failed to process any of the generated images.")
            raise RuntimeError("Failed to process any of the generated images.")

        logger.debug(f"Returning {len(output_images)} images with shape: {output_images[0].shape}")
        return (output_images,)

NODE_CLASS_MAPPINGS = {
    "FalAPIFluxNode": FalAPIFluxNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FalAPIFluxNode": "Fal API Flux"
}