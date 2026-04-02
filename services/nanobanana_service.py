import requests
from urllib.parse import urljoin

from config import NANO_BANANA_API_KEY, PUBLIC_BASE_URL


class NanoBananaService:
    BASE_URL = "https://api.nanobananaapi.ai/api/v1/nanobanana"

    def _build_group_safe_prompt(self, user_prompt: str) -> str:
        return f"""
IMPORTANT SUBJECT PRESERVATION RULES:
- Preserve EVERY visible person from the reference photo.
- If the reference photo contains 2 or more people, keep the EXACT SAME number of people in the result.
- Do not remove, merge, replace, crop out, blur, or invent any person.
- Preserve each person's identity, facial features, skin tone, hairstyle, glasses, facial hair, and approximate age.
- Apply the requested style or transformation to ALL visible people consistently.
- Keep the group composition, left-to-right order, body placement, and framing as close as possible to the reference image.
- Do not turn a group photo into a single-person portrait.
- Preserve the relationship, pose logic, and spacing between all visible people.
- If multiple people appear in the reference image, the final image must still clearly show all of them.

STYLE REQUEST:
{user_prompt}
""".strip()

    def submit_image_edit_task(self, prompt: str, image_url: str, aspect_ratio: str):
        url = f"{self.BASE_URL}/generate-2"

        payload = {
            "prompt": self._build_group_safe_prompt(prompt),
            "imageUrls": [image_url],
            "aspectRatio": aspect_ratio,
            "resolution": "1K",
            "googleSearch": False,
            "outputFormat": "jpg",
            "callBackUrl": urljoin(
                f"{PUBLIC_BASE_URL.strip().rstrip('/')}/",
                "nanobanana/callback",
            ),
        }

        headers = {
            "Authorization": f"Bearer {NANO_BANANA_API_KEY}",
            "Content-Type": "application/json",
        }

        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()

        data = response.json()

        if data.get("code") != 200:
            raise Exception(
                f"Nano Banana generate-2 gagal: {data.get('message') or data.get('msg') or 'Unknown error'}"
            )

        task_id = data.get("data", {}).get("taskId")
        if not task_id:
            raise Exception("Nano Banana generate-2 tidak mengembalikan taskId")

        return {
            "task_id": task_id,
            "raw_response": data,
        }

    def get_task_details(self, task_id: str):
        url = f"{self.BASE_URL}/record-info"
        headers = {
            "Authorization": f"Bearer {NANO_BANANA_API_KEY}",
        }

        response = requests.get(
            url,
            params={"taskId": task_id},
            headers=headers,
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def download_result_image(self, image_url: str, output_path: str):
        response = requests.get(image_url, stream=True, timeout=120)
        response.raise_for_status()

        with open(output_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    file.write(chunk)