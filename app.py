# C:\Users\tamil\Documents\Nalam Backend\app.py (Python Backend)

from flask import Flask, jsonify, request
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
import datetime
import re
import requests
from bs4 import BeautifulSoup

# Import blueprints
# from .invoice_routes import invoice_bp # No longer needed if integrated directly
# from .product_routes import product_bp # No longer needed if integrated directly
# from .customer_routes import customer_bp # No longer needed if integrated directly
# from .settings_routes import settings_bp # No longer needed if integrated directly

from waitress import serve # NEW: Import serve from waitress

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
NALAM_FOODS_URL = "https://nalamfoodsusa.com"
FIREBASE_CRED_FILE = "nalam-invoice-d4b10ee1417b.json" # Assumes this is in C:\Users\tamil\Documents\Nalam Backend\


# --- Firebase Service ---
db = None
_firebase_app_instance = None

def initialize_firebase_app():
    global _firebase_app_instance
    if _firebase_app_instance is not None:
        print("Firebase app already initialized.")
        return _firebase_app_instance

    try:
        cred = credentials.Certificate(FIREBASE_CRED_FILE)
        _firebase_app_instance = firebase_admin.initialize_app(cred)
        print("Firebase Admin App initialized successfully!")
        return _firebase_app_instance
    except Exception as e:
        print(f"Error initializing Firebase Admin App: {e}")
        print(f"Please ensure '{FIREBASE_CRED_FILE}' is in the correct directory (C:\\Users\\tamil\\Documents\\Nalam Backend\\) and is valid.")
        _firebase_app_instance = None
        return None

# Initialize Firebase and get the db client directly here
try:
    firebase_admin_app = initialize_firebase_app()
    db = firestore.client(firebase_admin_app)
    print("Firestore client initialized and ready.")
except Exception as e:
    print(f"Error getting Firestore client: {e}")
    db = None


# --- Models ---
class Category:
    def __init__(self, id, name):
        self.id = id
        self.name = name

allCategories = [
    Category(id='leaf_plate', name='Leaf Plates/Bowls'),
    Category(id='puttur_flour', name='Puttur Flour'),
    Category(id='rice_flakes', name='Rice Flakes'),
    Category(id='thokku', name='Thokku/Coffee/Soup'),
    Category(id='spices_masala', name='Spices/Masala'),
    Category(id='millets', name='Millets'),
    Category(id='sweets_snacks', name='Sweets/Snacks'),
    Category(id='dhal', name='Dhal'),
    Category(id='oil', name='Oil'),
    Category(id='sweetener', name='Sweeteners'),
    Category(id='health', name='Health'),
    Category(id='rice', name='Rice'),
    Category(id='others', name='Others'),
]


# --- Scraping Service ---
def scrape_products():
    all_products_data = []
    page = 1

    while True:
        try:
            scrape_url = f"{NALAM_FOODS_URL}/collections/all?page={page}"
            print(f"Scraping page {page}: {scrape_url}")
            response = requests.get(scrape_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            product_elements = soup.find_all('div', class_='product-card-wrapper') or \
                               soup.find_all('div', class_='product-card')
            if not product_elements:
                break

            for product_element in product_elements:
                product_name, product_price = parse_product(product_element)
                category_id = match_category(product_name)
                all_products_data.append({
                    "id": f"scraped_p{len(all_products_data)+1}",
                    "name": product_name,
                    "price": product_price,
                    "categoryId": category_id
                })
            page += 1
        except Exception as e:
            print(f"Error scraping page {page}: {e}")
            break

    return all_products_data

def parse_product(element):
    name_tag = element.find('a', class_='full-width-link')
    product_name = name_tag.find('span', class_='visually-hidden').get_text(strip=True) \
                   if name_tag and name_tag.find('span', class_='visually-hidden') \
                   else name_tag.get_text(strip=True) if name_tag else "Unknown Product"
    
    price_tag = element.find('span', class_='price-item--regular') or \
                element.find('span', class_='price-item')
    price_text = price_tag.get_text(strip=True) if price_tag else "0"
    price_value = float(re.sub(r'[^\d.]', '', price_text) or 0)
    
    return product_name, price_value

def match_category(product_name):
    name_lower = product_name.lower()
    for cat in allCategories:
        cat_lower = cat.name.lower()
        if cat_lower in name_lower:
            return cat.id
    if 'rice' in name_lower: return 'rice'
    if 'oil' in name_lower: return 'oil'
    if 'millet' in name_lower: return 'millets'
    if 'snack' in name_lower or 'sweet' in name_lower: return 'sweets_snacks'
    if 'dal' in name_lower: return 'dhal'
    if 'jaggery' in name_lower: return 'sweetener'
    if 'health' in name_lower: return 'health'
    return 'others'

def get_hardcoded_products():
    return [
        {"id": "fallback_p1", "name": "Fallback Rice - 5kg", "price": 10.00, "categoryId": "rice"},
        {"id": "fallback_p2", "name": "Fallback Oil - 1L", "price": 5.00, "categoryId": "oil"},
        {"id": "fallback_p3", "name": "Fallback Spices - 100g", "price": 2.50, "categoryId": "spices_masala"},
        {"id": "fallback_p4", "name": "Fallback Millet Mix", "price": 7.00, "categoryId": "millets"},
        {"id": "fallback_p5", "name": "Fallback Sweet Snack", "price": 3.00, "categoryId": "sweets_snacks"},
    ]


# --- Product Routes (Integrated directly into app.py) ---
@app.route('/products')
def get_products_route(): # Renamed to avoid conflict with scraping_service function
    products = scrape_products()
    if not products:
        return jsonify(get_hardcoded_products()), 200
    return jsonify(products), 200


# --- Invoice Routes (Integrated directly into app.py) ---
@app.route('/invoices', methods=['POST'])
def create_invoice():
    if db is None:
        print("Error: Firestore not initialized in create_invoice.")
        return jsonify({"error": "Firestore not initialized"}), 500
    try:
        data = request.get_json()
        
        today_str = datetime.datetime.now().strftime("%Y%m%d")
        
        invoices_today_docs = db.collection('invoices').where('invoiceDatePrefix', '==', today_str).get()
        count_today = len(invoices_today_docs)
        
        invoice_suffix = str(count_today + 1).zfill(3)
        invoice_number = f"{today_str}{invoice_suffix}"
        
        data['invoiceNumber'] = invoice_number
        data['invoiceDatePrefix'] = today_str
        data['timestamp'] = firestore.SERVER_TIMESTAMP
        
        db.collection('invoices').document(invoice_number).set(data)
        
        print(f"Invoice saved to Firestore with ID: {invoice_number}")
        return jsonify({"invoiceNumber": invoice_number}), 201
    except Exception as e:
        print(f"Error saving invoice: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/invoices', methods=['GET'])
def get_invoices():
    if db is None:
        print("Error: Firestore not initialized in get_invoices.")
        return jsonify({"error": "Firestore not initialized"}), 500
    try:
        mobile_number_filter = request.args.get('mobileNumber')
        date_filter = request.args.get('date')
        invoice_number_filter = request.args.get('invoiceNumber')

        query = db.collection('invoices')

        if mobile_number_filter:
            query = query.where('mobileNumber', '==', mobile_number_filter)
        if date_filter:
            query = query.where('invoiceDatePrefix', '==', date_filter.replace('-', ''))
        if invoice_number_filter:
            query = query.where('invoiceNumber', '==', invoice_number_filter)
        
        docs = query.order_by('invoiceNumber', direction=firestore.Query.DESCENDING).stream()
        
        invoices = []
        for doc in docs:
            invoice = doc.to_dict()
            invoice['invoiceNumber'] = doc.id
            if 'timestamp' in invoice and hasattr(invoice['timestamp'], 'isoformat'):
                invoice['timestamp'] = invoice['timestamp'].isoformat()
            
            invoices.append(invoice)
        
        print(f"Fetched {len(invoices)} invoices from Firestore with filters.")
        return jsonify(invoices), 200
    except Exception as e:
        print(f"Error fetching invoices: {e}")
        return jsonify({"error": str(e)}), 500


# --- Customer Routes (Integrated directly into app.py) ---
@app.route('/customers', methods=['POST'])
def save_or_update_customer():
    if db is None:
        print("Error: Firestore not initialized in save_or_update_customer.")
        return jsonify({"error": "Firestore not initialized"}), 500
    try:
        customer_data = request.get_json()
        mobile_number = customer_data.get('mobileNumber')
        name = customer_data.get('name')
        address = customer_data.get('address')

        if not mobile_number:
            return jsonify({"error": "Mobile number is required"}), 400

        customer_ref = db.collection('customers').document(mobile_number)
        customer_ref.set({
            'name': name,
            'address': address,
            'lastUpdated': firestore.SERVER_TIMESTAMP
        }, merge=True)

        print(f"Customer {mobile_number} details saved/updated.")
        return jsonify({"message": "Customer details saved/updated"}), 200
    except Exception as e:
        print(f"Error saving/updating customer: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/customers/<mobile_number>', methods=['GET'])
def get_customer(mobile_number):
    if db is None:
        print("Error: Firestore not initialized in get_customer.")
        return jsonify({"error": "Firestore not initialized"}), 500
    try:
        customer_doc = db.collection('customers').document(mobile_number).get()
        if customer_doc.exists:
            customer_data = customer_doc.to_dict()
            if 'lastUpdated' in customer_data and hasattr(customer_data['lastUpdated'], 'isoformat'):
                customer_data['lastUpdated'] = customer_data['lastUpdated'].isoformat()
            print(f"Fetched customer data for {mobile_number}.")
            return jsonify(customer_data), 200
        else:
            print(f"Customer {mobile_number} not found.")
            return jsonify({"message": "Customer not found"}), 404
    except Exception as e:
        print(f"Error fetching customer: {e}")
        return jsonify({"error": str(e)}), 500


# --- NEW: Settings Routes (Integrated directly into app.py) ---
@app.route('/settings', methods=['POST'])
def save_settings():
    if db is None:
        print("Error: Firestore not initialized in save_settings.")
        return jsonify({"error": "Firestore not initialized"}), 500
    try:
        settings_data = request.get_json()
        # Store settings in a single document, e.g., 'company_profile'
        db.collection('settings').document('company_profile').set(settings_data)
        print("Company settings saved successfully.")
        return jsonify({"message": "Settings saved successfully"}), 200
    except Exception as e:
        print(f"Error saving settings: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/settings', methods=['GET'])
def get_settings():
    if db is None:
        print("Error: Firestore not initialized in get_settings.")
        return jsonify({"error": "Firestore not initialized"}), 500
    try:
        settings_doc = db.collection('settings').document('company_profile').get()
        if settings_doc.exists:
            settings_data = settings_doc.to_dict()
            print("Company settings fetched successfully.")
            return jsonify(settings_data), 200
        else:
            print("Company settings document not found.")
            return jsonify({"message": "Settings not found"}), 404
    except Exception as e:
        print(f"Error fetching settings: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    # Use Waitress for serving the Flask app on Windows
    serve(app, host='0.0.0.0', port=5000)