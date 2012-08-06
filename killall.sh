#!/bin/bash
echo "Killing babysitter.py and iam_logger.py..."
kill $(pidof -x babysitter.py) $(pidof -x iam_logger.py)
echo "Done."
