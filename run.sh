#!/bin/bash
cd "$(dirname "$0")"
pip3 install -q -r requirements.txt
python3 app.py
