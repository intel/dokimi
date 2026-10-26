#!/bin/bash
pip3 install --break-system-packages pytest==9.0.2 pytest-json-ctrf==0.3.6
mkdir -p /logs/verifier
if pytest --ctrf /logs/verifier/ctrf.json /verifier/test_outputs.py -rA -v; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
exit 0
