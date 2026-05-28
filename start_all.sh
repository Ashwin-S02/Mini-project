#!/bin/bash

echo "=========================================="
echo "Starting Maps Lead Scraper"
echo "=========================================="
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "ERROR: Virtual environment not found!"
    echo "Please run: python -m venv venv"
    echo "Then: source venv/bin/activate"
    echo "Then: pip install -r requirements.txt"
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Start backend in background
echo "Starting Backend Server (Port 8081)..."
python main.py > /dev/null 2>&1 &
BACKEND_PID=$!

sleep 3

# Start frontend in background
echo "Starting Frontend Server (Port 3000)..."
python start_frontend.py > /dev/null 2>&1 &
FRONTEND_PID=$!

sleep 2

echo ""
echo "=========================================="
echo "Both servers are running!"
echo "=========================================="
echo ""
echo "Backend:  http://localhost:8081"
echo "Frontend: http://localhost:3000"
echo ""
echo "Opening browser..."
sleep 2

# Open browser (works on most systems)
if command -v xdg-open > /dev/null; then
    xdg-open http://localhost:3000
elif command -v open > /dev/null; then
    open http://localhost:3000
else
    echo "Please open http://localhost:3000 in your browser"
fi

echo ""
echo "Press Ctrl+C to stop all servers..."

# Wait for Ctrl+C
trap "echo ''; echo 'Stopping servers...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo 'Done!'; exit" INT

wait
