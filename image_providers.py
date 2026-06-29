"""Image generation provider abstraction — local (A1111/ComfyUI), ImageRouter, or none."""

from __future__ import annotations

import base64
import json
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent
API_DIR = ROOT / "api"

VALID_IMAGE_PROVIDERS = frozenset({"local", "imagerouter", "none"})
VALID_LOCAL_BACKENDS = frozenset({"automatic1111", "comfyui"})

ISLAMIC_NEGATIVE_PROMPT = (
    "clear face, detailed face, portrait, selfie, text, letters, watermark, logo, "
    "caption, words, nsfw, nude, inappropriate"
)


def _read_secret(filename: str) -> str:
    path = API_DIR / filename
    if not path.exists():
        raise RuntimeError(f"Missing API secret: {path.name}")
    return path.read_text(encoding="utf-8").strip()


def normalize_production_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Apply free_mode policy and block paid providers when disallowed."""
    s = dict(settings or {})
    if _parse_bool(s.get("free_mode"), False):
        s["allow_paid_providers"] = False
        s["image_provider"] = "local"
        s["local_image_enabled"] = True
    provider = str(s.get("image_provider") or "local").strip().lower()
    if provider not in VALID_IMAGE_PROVIDERS:
        provider = "local"
    if not _parse_bool(s.get("allow_paid_providers"), False) and provider == "imagerouter":
        provider = "local"
    s["image_provider"] = provider
    return s


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "yes", "on"}


class ImageProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate_bytes(
        self,
        prompt: str,
        width: int,
        height: int,
        settings: dict[str, Any],
        *,
        seed: int | None = None,
        negative_prompt: str = "",
    ) -> bytes:
        ...

    def label(self, settings: dict[str, Any]) -> str:
        return self.name


class NoneImageProvider(ImageProvider):
    name = "none"

    def generate_bytes(
        self,
        prompt: str,
        width: int,
        height: int,
        settings: dict[str, Any],
        *,
        seed: int | None = None,
        negative_prompt: str = "",
    ) -> bytes:
        raise RuntimeError("image_provider=none — AI image generation disabled")


class ImageRouterProvider(ImageProvider):
    name = "imagerouter"

    def generate_bytes(
        self,
        prompt: str,
        width: int,
        height: int,
        settings: dict[str, Any],
        *,
        seed: int | None = None,
        negative_prompt: str = "",
    ) -> bytes:
        if not _parse_bool(settings.get("allow_paid_providers"), False):
            raise RuntimeError(
                "ImageRouter blocked — set allow_paid_providers=true or use image_provider=local"
            )
        model = str(settings.get("imagerouter_model") or "black-forest-labs/FLUX-1-schnell").strip()
        if model.lower() in {"test/test", "test", ""}:
            model = "black-forest-labs/FLUX-1-schnell"
        api_key = _read_secret("imagerouter_secret.txt")
        response = requests.post(
            "https://api.imagerouter.io/v1/openai/images/generations",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "prompt": prompt,
                "model": model,
                "size": f"{width}x{height}",
                "response_format": "url",
                "output_format": "webp",
            },
            timeout=180,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            err = payload["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"ImageRouter: {message}")
        data = payload.get("data")
        if not data:
            raise RuntimeError(f"ImageRouter: unexpected response keys {list(payload.keys())}")
        url = str(data[0]["url"])
        img_resp = requests.get(url, timeout=120)
        img_resp.raise_for_status()
        return img_resp.content

    def label(self, settings: dict[str, Any]) -> str:
        model = str(settings.get("imagerouter_model") or "black-forest-labs/FLUX-1-schnell").strip()
        return f"ImageRouter / {model}"


class LocalAutomatic1111Provider(ImageProvider):
    name = "local-a1111"

    def _base_url(self, settings: dict[str, Any]) -> str:
        return str(settings.get("local_image_api_url") or "http://127.0.0.1:7860").rstrip("/")

    def generate_bytes(
        self,
        prompt: str,
        width: int,
        height: int,
        settings: dict[str, Any],
        *,
        seed: int | None = None,
        negative_prompt: str = "",
    ) -> bytes:
        if not _parse_bool(settings.get("local_image_enabled"), True):
            raise RuntimeError("local_image_enabled=false — enable local GPU generation")
        base = self._base_url(settings)
        steps = max(1, min(60, int(settings.get("local_image_steps") or 20)))
        cfg = float(settings.get("local_image_cfg_scale") or 7.0)
        neg = (negative_prompt or ISLAMIC_NEGATIVE_PROMPT).strip()
        payload: dict[str, Any] = {
            "prompt": prompt,
            "negative_prompt": neg,
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": cfg,
            "sampler_name": str(settings.get("local_image_sampler") or "DPM++ 2M Karras"),
            "seed": seed if seed is not None else -1,
            "batch_size": 1,
            "n_iter": 1,
        }
        model = str(settings.get("local_image_model") or "").strip()
        if model:
            payload["override_settings"] = {"sd_model_checkpoint": model}
        response = requests.post(
            f"{base}/sdapi/v1/txt2img",
            json=payload,
            timeout=int(settings.get("local_image_timeout_sec") or 600),
        )
        if response.status_code == 404:
            raise RuntimeError(
                f"Automatic1111 API not found at {base}/sdapi/v1/txt2img — "
                "start WebUI with --api flag"
            )
        response.raise_for_status()
        data = response.json()
        images = data.get("images") or []
        if not images:
            raise RuntimeError("Local A1111 returned no images")
        raw = images[0]
        if "," in raw:
            raw = raw.split(",", 1)[1]
        return base64.b64decode(raw)

    def label(self, settings: dict[str, Any]) -> str:
        model = str(settings.get("local_image_model") or "default checkpoint").strip()
        return f"Local A1111 @ {self._base_url(settings)} / {model}"


class LocalComfyUIProvider(ImageProvider):
    """ComfyUI via /prompt queue — requires configs/comfyui_txt2img_api.json workflow."""

    name = "local-comfyui"

    def _base_url(self, settings: dict[str, Any]) -> str:
        return str(settings.get("local_image_api_url") or "http://127.0.0.1:8188").rstrip("/")

    def _workflow_path(self, settings: dict[str, Any]) -> Path:
        custom = str(settings.get("local_image_comfy_workflow") or "").strip()
        if custom:
            p = Path(custom)
            if p.exists():
                return p
        return ROOT / "configs" / "comfyui_txt2img_api.json"

    def generate_bytes(
        self,
        prompt: str,
        width: int,
        height: int,
        settings: dict[str, Any],
        *,
        seed: int | None = None,
        negative_prompt: str = "",
    ) -> bytes:
        if not _parse_bool(settings.get("local_image_enabled"), True):
            raise RuntimeError("local_image_enabled=false — enable local GPU generation")
        workflow_path = self._workflow_path(settings)
        if not workflow_path.exists():
            raise RuntimeError(
                f"ComfyUI workflow missing: {workflow_path} — use local_image_backend=automatic1111 "
                "or add a workflow JSON"
            )
        base = self._base_url(settings)
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        workflow = _inject_comfy_prompt(workflow, prompt, negative_prompt or ISLAMIC_NEGATIVE_PROMPT, width, height, seed)
        client_id = str(uuid.uuid4())
        queued = requests.post(
            f"{base}/prompt",
            json={"prompt": workflow, "client_id": client_id},
            timeout=30,
        )
        queued.raise_for_status()
        prompt_id = queued.json().get("prompt_id")
        if not prompt_id:
            raise RuntimeError("ComfyUI did not return prompt_id")
        return _poll_comfy_output(base, prompt_id, timeout=int(settings.get("local_image_timeout_sec") or 600))

    def label(self, settings: dict[str, Any]) -> str:
        return f"Local ComfyUI @ {self._base_url(settings)}"


def _inject_comfy_prompt(
    workflow: dict[str, Any],
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int | None,
) -> dict[str, Any]:
    """Patch placeholder nodes in exported ComfyUI API workflow."""
    wf = json.loads(json.dumps(workflow))
    for node in wf.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}
        class_type = node.get("class_type") or ""
        if class_type == "CLIPTextEncode" and inputs.get("text") == "__PROMPT__":
            inputs["text"] = prompt
        if class_type == "CLIPTextEncode" and inputs.get("text") == "__NEGATIVE__":
            inputs["text"] = negative
        if class_type == "EmptyLatentImage":
            inputs["width"] = width
            inputs["height"] = height
        if class_type == "KSampler" and seed is not None:
            inputs["seed"] = seed
    return wf


def _poll_comfy_output(base: str, prompt_id: str, timeout: int = 600) -> bytes:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        hist = requests.get(f"{base}/history/{prompt_id}", timeout=30)
        hist.raise_for_status()
        entry = hist.json().get(prompt_id)
        if not entry:
            time.sleep(1.0)
            continue
        outputs = entry.get("outputs") or {}
        for out in outputs.values():
            for img in out.get("images") or []:
                params = {
                    "filename": img["filename"],
                    "subfolder": img.get("subfolder") or "",
                    "type": img.get("type") or "output",
                }
                view = requests.get(f"{base}/view", params=params, timeout=60)
                view.raise_for_status()
                return view.content
        status = entry.get("status") or {}
        if status.get("status_str") == "error":
            raise RuntimeError(f"ComfyUI generation failed: {status.get('messages')}")
        time.sleep(1.0)
    raise RuntimeError("ComfyUI generation timed out")


class LocalImageProvider(ImageProvider):
    """Routes to Automatic1111 or ComfyUI based on local_image_backend."""

    name = "local"

    def _delegate(self, settings: dict[str, Any]) -> ImageProvider:
        backend = str(settings.get("local_image_backend") or "automatic1111").strip().lower()
        if backend == "comfyui":
            return LocalComfyUIProvider()
        return LocalAutomatic1111Provider()

    def generate_bytes(
        self,
        prompt: str,
        width: int,
        height: int,
        settings: dict[str, Any],
        *,
        seed: int | None = None,
        negative_prompt: str = "",
    ) -> bytes:
        return self._delegate(settings).generate_bytes(
            prompt, width, height, settings, seed=seed, negative_prompt=negative_prompt
        )

    def label(self, settings: dict[str, Any]) -> str:
        return self._delegate(settings).label(settings)


def get_image_provider(settings: dict[str, Any] | None) -> ImageProvider:
    settings = normalize_production_settings(settings)
    provider = str(settings.get("image_provider") or "local").strip().lower()
    if provider == "imagerouter":
        return ImageRouterProvider()
    if provider == "none":
        return NoneImageProvider()
    return LocalImageProvider()


def image_provider_label(settings: dict[str, Any] | None) -> str:
    settings = normalize_production_settings(settings)
    return get_image_provider(settings).label(settings)


def image_provider_info(settings: dict[str, Any] | None) -> dict[str, Any]:
    settings = normalize_production_settings(settings)
    return {
        "image_provider": settings.get("image_provider"),
        "free_mode": _parse_bool(settings.get("free_mode"), False),
        "allow_paid_providers": _parse_bool(settings.get("allow_paid_providers"), False),
        "local_image_backend": settings.get("local_image_backend"),
        "local_image_api_url": settings.get("local_image_api_url"),
        "local_image_model": settings.get("local_image_model"),
        "label": image_provider_label(settings),
    }


def generate_image_bytes(
    prompt: str,
    width: int,
    height: int,
    settings: dict[str, Any] | None = None,
    *,
    seed: int | None = None,
    negative_prompt: str = "",
) -> bytes:
    settings = normalize_production_settings(settings)
    provider = get_image_provider(settings)
    return provider.generate_bytes(
        prompt,
        width,
        height,
        settings,
        seed=seed,
        negative_prompt=negative_prompt,
    )


# Backward-compatible alias used by older imports
def _imagerouter_model(settings: dict[str, Any] | None = None) -> str:
    settings = normalize_production_settings(settings)
    if str(settings.get("image_provider")) == "imagerouter":
        return str(settings.get("imagerouter_model") or "black-forest-labs/FLUX-1-schnell")
    return image_provider_label(settings)
