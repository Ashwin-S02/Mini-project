"""
Simple HTTP server to serve the frontend locally
This avoids CORS issues when opening HTML files directly
"""
import http.server
import socketserver
import os
import sys

PORT = 3000

if __name__ == "__main__":
    # Change to frontend directory
    frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frontend')
    os.chdir(frontend_dir)
    
    Handler = http.server.SimpleHTTPRequestHandler
    
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print("=" * 60)
        print("Frontend Server Started!")
        print("=" * 60)
        print(f"\n✅ Frontend running at: http://localhost:{PORT}")
        print(f"✅ Backend should be running at: http://localhost:8081")
        print(f"\n📂 Serving files from: {frontend_dir}")
        print(f"\n🌐 Open your browser and go to: http://localhost:{PORT}")
        print("\nPress Ctrl+C to stop the server\n")
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n👋 Shutting down server...")
            sys.exit(0)
