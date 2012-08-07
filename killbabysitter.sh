#!/bin/bash
echo "Killing babysitter.py..."
kill $(pidof -x babysitter.py)
echo "Done."

