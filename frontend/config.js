// Backend API Configuration
// Change this based on your environment

const API_CONFIG = {
    // VM backend URL (Google Compute Engine e2-micro)
    VM: 'http://34.171.84.134:8080',
    
    // Cloud Run backend URL (has timeout issues)
    CLOUD_RUN: 'https://lead-scraper-backend-1089535354620.us-central1.run.app',
    
    // Local development backend URL
    LOCAL: 'http://localhost:8081',
    
    // Current environment - change based on where backend is running
    CURRENT: 'LOCAL'  // Options: 'VM', 'CLOUD_RUN', or 'LOCAL'
};

// Get the active backend URL
const BACKEND_URL = API_CONFIG[API_CONFIG.CURRENT];

console.log(`🔧 Backend URL: ${BACKEND_URL}`);
