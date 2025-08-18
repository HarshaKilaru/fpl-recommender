FPL Recommender

[![CI](https://github.com/<USER>/fpl-recommender/actions/workflows/ci.yml/badge.svg)](https://github.com/<USER>/fpl-recommender/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10-blue)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)


A lightweight Fantasy Premier League recommender: Python ranking core + a tiny FastAPI backend with a built-in browser UI.



https://user-images.example/screenshot-demo.gif  <!-- (optional) replace with a GIF later) -->



# Features

\- Pulls live player and fixture data from the official FPL API

\- Composite scoring: form, PPG, fixture outlook, ICT, availability penalty

\- Respect budget + “max 3 from a team”

\- CLI and REST API with a minimal UI

\- CSV export

\- Simple 15-minute on-disk cache



\ Quickstart



```bash

\ create venv (Windows cmd)

python -m venv .venv

.venv\\Scripts\\activate



pip install -r requirements.txt



\# CLI example

python -m src.fpl\_recommender.app --budget 12.5 --need "2:1,3:1" --exclude "" --max-from-team 3



\# API

uvicorn src.fpl\_recommender.server:app --reload

\ open http://127.0.0.1:8000/  (UI)

\ or  http://127.0.0.1:8000/docs (Swagger)



