import os
import json
import gspread

def check_gs_access():
    creds_file = 'credentials.json'
    if not os.path.exists(creds_file):
        print(f"Error: {creds_file} not found.")
        return

    try:
        with open(creds_file, 'r') as f:
            creds_dict = json.load(f)
        
        client_email = creds_dict.get('client_email')
        print(f"Auth: Attempting login for {client_email}...")
        gc = gspread.service_account_from_dict(creds_dict)
        print("Auth: Success with Google service account.")
        
        spreadsheets = gc.openall()
        if not spreadsheets:
            print("Warning: No spreadsheets found. Make sure you've shared a sheet with the client_email:")
            print(f"   {client_email}")
        else:
            print(f"Found {len(spreadsheets)} spreadsheet(s):")
            for sh in spreadsheets:
                print(f"   - {sh.title}")
                
        # Also try opening by key as done in our main.py
        target_key = "1BCEot7vu1-H0XQ7EE9C6P3uHJdtABD8VEO87N_-WPOc"
        print(f"Check: Attempting to open target sheet by key '{target_key}'...")
        try:
            sh = gc.open_by_key(target_key)
            print(f"Success: Opened target sheet by key: '{sh.title}'")
        except Exception as e:
            print(f"Error: Target sheet by key NOT found or not shared with service account: {e}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_gs_access()
