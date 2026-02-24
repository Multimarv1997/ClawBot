#!/usr/bin/env python3
"""Kleiner HTTP-Proxy: nutzt MemoryEngine und reduziert Gemini Calls via Cache."""

from __future__ import annotations

import os
from typing import Any, Dict

import requests
from flask import Flask, jsonify, request

from memory_engine import MemoryEngine


GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

app = Flask(__name__)
engine = MemoryEngine()
engine.init_db()


def call_gemini(prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return "[Demo-Antwort] GEMINI_API_KEY fehlt. Bitte nur als ENV setzen, nie im Code hardcoden."

    payload: Dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
    }
    resp = requests.post(
        GEMINI_ENDPOINT,
        params={"key": api_key},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return str(data)


@app.post("/chat")
def chat() -> Any:
    body = request.get_json(force=True)
    user_prompt = body.get("prompt", "").strip()
    if not user_prompt:
        return jsonify({"error": "prompt fehlt"}), 400

    engine.remember_turn("user", user_prompt)

    cached = engine.get_cached(user_prompt)
    if cached:
        engine.remember_turn("assistant", cached)
        return jsonify({"response": cached, "source": "cache"})

    context = engine.build_context(query=user_prompt)
    full_prompt = f"{context}\n\nUser: {user_prompt}"
    answer = call_gemini(full_prompt)

    engine.remember_turn("assistant", answer)
    engine.set_cached(user_prompt, answer)
    return jsonify({"response": answer, "source": "gemini"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
