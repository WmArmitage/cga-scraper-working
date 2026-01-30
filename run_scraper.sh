#!/bin/bash

# 1. Navigate to the scraper directory
cd /root/cga-scraper

# 2. Activate the virtual environment
source venv/bin/activate

# 3. Run the scraper (pointing to your actual filename)
python3 cga_scraper.py >> scraper.log 2>&1

# 4. Copy the fresh file to the web directory
cp /root/cga-scraper/cga.ics /var/www/html/cga.ics

# 5. Ensure the web server can read it
chmod 644 /var/www/html/cga.ics
