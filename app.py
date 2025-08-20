from flask import Flask, jsonify, request
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
import datetime
import re
import requests
from bs4 import BeautifulSoup
import os
if os.path.exists(".env"):
    from dotenv import load_dotenv
    load_dotenv()
import threading
import time
import json
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import uuid
import base64
from pypdf import PdfReader
import io
from config import Config

app = Flask(__name__)
CORS(app)

app.config.from_object(Config)
NALAM_FOODS_URL = app.config['NALAM_FOODS_URL']
FIREBASE_CRED_FILE = app.config['FIREBASE_CRED_FILE']
firebase_cred_path = FIREBASE_CRED_FILE

# Configure your Gemini API key here
genai.configure(api_key=app.config['GEMINI_API_KEY'])


# --- Firebase Service ---
db = None
_firebase_app_instance = None

def initialize_firebase_app():
    global _firebase_app_instance
    if _firebase_app_instance is not None:
        print("Firebase app already initialized.")
        return _firebase_app_instance

    try:
        # cred = credentials.Certificate(firebase_cred_path)
        # On Google Cloud, the SDK automatically finds the service account credentials.
        _firebase_app_instance = firebase_admin.initialize_app()
        print("Firebase Admin App initialized successfully!")
        return _firebase_app_instance
    except Exception as e:
        import traceback
        print("!!!!!!!!!! DETAILED FIREBASE INITIALIZATION ERROR !!!!!!!!!!!")
        print(f"Error initializing Firebase Admin App: {e}")
        print(f"Please ensure '{FIREBASE_CRED_FILE}' is in the correct directory and is valid.")
        traceback.print_exc()
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        _firebase_app_instance = None
        raise e

# CORRECTED INITIALIZATION BLOCK:
try:
    firebase_admin_app = initialize_firebase_app()
    if firebase_admin_app:
        db = firestore.client(firebase_admin_app)
        print("Firestore client initialized and ready.")
    else:
        raise Exception("Firebase Admin App failed to initialize and returned None.")
except Exception as e:
    print(f"Error initializing Firebase: {e}")
    db = None
    import traceback
    print("!!!!!!!!!! APPLICATION FAILED TO START !!!!!!!!!!!")
    print(f"A critical error occurred during startup: {e}")
    traceback.print_exc()
    print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    # Re-raise the exception to ensure app fails clearly
    raise e



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
                safe_id = re.sub(r'[^\w-]', '', product_name.replace(' ', '_').lower())
                all_products_data.append({
                    "id": safe_id,
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


# --- Product Synchronization Service ---
def synchronize_products():
    print("--- DEBUG: Starting hourly product synchronization... ---")
    if db is None:
        print("Error: Firestore not initialized. Skipping synchronization.")
        return
    try:
        scraped_products = scrape_products()
        if not scraped_products:
            print("No products scraped. Skipping synchronization.")
            return

        product_ref = db.collection('products')
        
        firestore_products = {doc.id: doc.to_dict() for doc in product_ref.stream()}
        scraped_product_ids = {p['id'] for p in scraped_products}

        for product in scraped_products:
            product_id = product['id']
            if product_id in firestore_products:
                firestore_product = firestore_products[product_id]
                if firestore_product.get('price') != product['price'] or \
                   firestore_product.get('categoryId') != product['categoryId']:
                    print(f"Updating product: {product_id}")
                    product_ref.document(product_id).update({
                        'name': product['name'],
                        'price': product['price'],
                        'categoryId': product['categoryId'],
                        'lastUpdated': firestore.SERVER_TIMESTAMP
                    })
            else:
                print(f"Adding new product: {product_id}")
                product_ref.document(product_id).set(product)

        for product_id in firestore_products.keys():
            if product_id not in scraped_product_ids:
                print(f"Deleting product: {product_id}")
                product_ref.document(product_id).delete()

        print("--- DEBUG: Product synchronization complete. ---")

    except Exception as e:
        print(f"Error during product synchronization: {e}")

def start_scheduler():
    synchronize_products()
    while True:
        time.sleep(3600)
        synchronize_products()



# threading.Thread(target=start_scheduler, daemon=True).start()


@app.route('/healthz')
def healthz():
    """A simple health check endpoint."""
    return "OK", 200


# --- Product Routes (Integrated directly into app.py) ---
@app.route('/products')
def get_products_route():
    if db is None:
        print("Error: Firestore not initialized. Cannot fetch products.")
        return jsonify({"error": "Firestore not initialized"}), 500
    
    try:
        docs = db.collection('products').stream()
        products = [doc.to_dict() for doc in docs]
        if products:
            print(f"Returning {len(products)} products from Firestore.")
            return jsonify(products), 200
        else:
            print("No products found in Firestore. Returning hardcoded products.")
            return jsonify(get_hardcoded_products()), 200

    except Exception as e:
        print(f"Error fetching products from Firestore: {e}")
        return jsonify({"error": str(e)}), 500


# --- Invoice Routes (Integrated directly into app.py) ---
@app.route('/invoices', methods=['POST'])
def create_invoice():
    if db is None:
        print("Error: Firestore not initialized in create_invoice.")
        return jsonify({"error": "Firestore not initialized"}), 500
    try:
        data = request.get_json()
        
        today_str = datetime.datetime.now().strftime("%Y%m%d")
        
        invoices_today_docs = db.collection('invoices').where(filter=firestore.FieldFilter('invoiceDatePrefix', '==', today_str)).get()
        count_today = len(invoices_today_docs)
        
        invoice_suffix = str(count_today + 1).zfill(3)
        invoice_number = f"{today_str}{invoice_suffix}"
        
        days_due = data.get('daysDue', 1)
        invoice_date = datetime.datetime.now()
        due_date = invoice_date + datetime.timedelta(days=days_due)
        
        data['invoiceNumber'] = invoice_number
        data['invoiceDatePrefix'] = today_str
        data['timestamp'] = firestore.SERVER_TIMESTAMP
        data['invoiceDate'] = invoice_date.isoformat()
        data['dueDate'] = due_date.isoformat()
        data['status'] = 'Unpaid'
        data['totalPaid'] = 0.0
        data['balanceAmount'] = data.get('totalAmount', 0.0)
        data['payments'] = []
        
        db.collection('invoices').document(invoice_number).set(data)
        
        print(f"Invoice saved to Firestore with ID: {invoice_number}")
        return jsonify({"invoiceNumber": invoice_number}), 201
    except Exception as e:
        print(f"Error saving invoice: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/invoices/<invoice_number>', methods=['GET'])
def get_invoice(invoice_number):
    """
    Fetches a single invoice document by its number.
    """
    if db is None:
        print("Error: Firestore not initialized in get_invoice.")
        return jsonify({"error": "Firestore not initialized"}), 500
    try:
        invoice_doc = db.collection('invoices').document(invoice_number).get()
        if invoice_doc.exists:
            invoice_data = invoice_doc.to_dict()
            print(f"Invoice {invoice_number} fetched successfully.")
            return jsonify(invoice_data), 200
        else:
            print(f"Invoice {invoice_number} not found.")
            return jsonify({"error": f"Invoice {invoice_number} not found"}), 404
    except Exception as e:
        print(f"Error fetching invoice {invoice_number}: {e}")
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
        year_filter = request.args.get('year')
        month_filter = request.args.get('month')

        query = db.collection('invoices')

        if mobile_number_filter:
            query = query.where(filter=firestore.FieldFilter('mobileNumber', '==', mobile_number_filter))
        if date_filter:
            query = query.where(filter=firestore.FieldFilter('invoiceDatePrefix', '==', date_filter.replace('-', '')))
        if invoice_number_filter:
            query = query.where(filter=firestore.FieldFilter('invoiceNumber', '==', invoice_number_filter))
        
        if year_filter:
            start_date_prefix = f"{year_filter}0101"
            end_date_prefix = f"{year_filter}1231"
            query = query.where(filter=firestore.FieldFilter('invoiceDatePrefix', '>=', start_date_prefix))
            query = query.where(filter=firestore.FieldFilter('invoiceDatePrefix', '<=', end_date_prefix))

        if month_filter and month_filter != 'All':
            if not year_filter:
                return jsonify({"error": "Month filter requires a year filter."}), 400
            
            start_date_prefix = f"{year_filter}{month_filter.zfill(2)}01"
            last_day_of_month = (datetime.date(int(year_filter), int(month_filter) % 12 + 1, 1) - datetime.timedelta(days=1)).day
            end_date_prefix = f"{year_filter}{month_filter.zfill(2)}{last_day_of_month}"
            
            query = query.where(filter=firestore.FieldFilter('invoiceDatePrefix', '>=', start_date_prefix))
            query = query.where(filter=firestore.FieldFilter('invoiceDatePrefix', '<=', end_date_prefix))
        
        docs = query.order_by('invoiceNumber', direction=firestore.Query.DESCENDING).stream()
        
        invoices = []
        for doc in docs:
            invoice = doc.to_dict()
            invoice['invoiceNumber'] = doc.id
            
            if 'timestamp' in invoice and isinstance(invoice['timestamp'], datetime.datetime):
                invoice['timestamp'] = invoice['timestamp'].isoformat()
            
            if 'invoiceDate' in invoice and isinstance(invoice['invoiceDate'], datetime.datetime):
                invoice['invoiceDate'] = invoice['invoiceDate'].isoformat()

            if 'dueDate' in invoice and isinstance(invoice['dueDate'], datetime.datetime):
                invoice['dueDate'] = invoice['dueDate'].isoformat()

            invoices.append(invoice)
        
        print(f"Fetched {len(invoices)} invoices from Firestore with filters.")
        return jsonify(invoices), 200
    except Exception as e:
        print(f"Error fetching invoices: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/invoices/<invoice_number>', methods=['PUT'])
def update_invoice(invoice_number):
    """
    Updates a full invoice document with all fields from the request body.
    """
    if db is None:
        print("Error: Firestore not initialized in update_invoice.")
        return jsonify({"error": "Firestore not initialized"}), 500
    
    try:
        data = request.get_json()
        invoice_ref = db.collection('invoices').document(invoice_number)
        invoice_doc = invoice_ref.get()

        if not invoice_doc.exists:
            print(f"Invoice {invoice_number} not found for update.")
            return jsonify({"error": f"Invoice {invoice_number} not found"}), 404

        invoice_ref.update(data)
        
        print(f"Invoice {invoice_number} updated with data: {data}")
        return jsonify({"message": f"Invoice {invoice_number} updated successfully"}), 200
    
    except Exception as e:
        print(f"Error updating invoice: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/invoices/<invoice_number>', methods=['DELETE'])
def delete_invoice(invoice_number):
    """
    Deletes an invoice from Firestore.
    """
    if db is None:
        print("Error: Firestore not initialized in delete_invoice.")
        return jsonify({"error": "Firestore not initialized"}), 500
    try:
        invoice_ref = db.collection('invoices').document(invoice_number)
        invoice_doc = invoice_ref.get()

        if not invoice_doc.exists:
            print(f"Invoice {invoice_number} not found for deletion.")
            return jsonify({"error": f"Invoice {invoice_number} not found"}), 404

        invoice_ref.delete()
        print(f"Invoice {invoice_number} deleted successfully from Firestore.")
        return jsonify({"message": f"Invoice {invoice_number} deleted successfully"}), 200
    except Exception as e:
        print(f"Error deleting invoice {invoice_number}: {e}")
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
        email = customer_data.get('email')
        tax_id = customer_data.get('taxId')
        tax_number = customer_data.get('taxNumber')
        is_generated_id = customer_data.get('isGeneratedId', False)

        if not mobile_number:
            return jsonify({"error": "Mobile number is required"}), 400

        customer_ref = db.collection('customers').document(mobile_number)
        customer_ref.set({
            'name': name,
            'address': address,
            'email': email,
            'taxId': tax_id,
            'taxNumber': tax_number,
            'isGeneratedId': is_generated_id,
            'lastUpdated': firestore.SERVER_TIMESTAMP
        }, merge=True)

        print(f"Customer {mobile_number} details saved/updated.")
        return jsonify({"message": "Customer details saved/updated"}), 200
    except Exception as e:
        print(f"Error saving/updating customer: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/customers', methods=['GET'])
def get_all_customers_or_search():
    """
    Fetches all customers if no search parameters are provided,
    otherwise searches by name (case-insensitive, starts with) or mobile number.
    """
    if db is None:
        print("Error: Firestore not initialized in get_all_customers_or_search.")
        return jsonify({"error": "Firestore not initialized"}), 500
    
    search_name = request.args.get('name', '').strip()
    mobile_number_filter = request.args.get('mobileNumber', '').strip()

    try:
        query = db.collection('customers')
        
        if search_name:
            end_name = search_name + '\uf8ff'
            query = query.where(filter=firestore.FieldFilter('name', '>=', search_name)).where(filter=firestore.FieldFilter('name', '<', end_name)).limit(10)
        elif mobile_number_filter:
            query = query.where(filter=firestore.FieldFilter('mobileNumber', '==', mobile_number_filter)).limit(1)
        
        docs = query.stream()
        
        customers = []
        for doc in docs:
            customer_data = doc.to_dict()
            customer_data['mobileNumber'] = doc.id
            
            if search_name and not customer_data['name'].lower().startswith(search_name.lower()):
                continue

            if 'lastUpdated' in customer_data and isinstance(customer_data['lastUpdated'], datetime.datetime):
                customer_data['lastUpdated'] = customer_data['lastUpdated'].isoformat()
            customers.append(customer_data)
        
        print(f"Found {len(customers)} customers.")
        return jsonify(customers), 200

    except Exception as e:
        print(f"Error fetching/searching for customers: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/customers/<mobile_number>', methods=['GET'])
def get_customer_by_mobile(mobile_number):
    """
    Fetches a single customer document by its mobile number.
    """
    if db is None:
        print("Error: Firestore not initialized in get_customer_by_mobile.")
        return jsonify({"error": "Firestore not initialized"}), 500
    try:
        customer_doc = db.collection('customers').document(mobile_number).get()
        if customer_doc.exists:
            customer_data = customer_doc.to_dict()
            customer_data['mobileNumber'] = customer_doc.id
            print(f"Customer {mobile_number} fetched successfully.")
            return jsonify(customer_data), 200
        else:
            print(f"Customer {mobile_number} not found.")
            return jsonify({"error": f"Customer {mobile_number} not found"}), 404
    except Exception as e:
        print(f"Error fetching customer {mobile_number}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/customers/<mobile_number>', methods=['DELETE'])
def delete_customer(mobile_number):
    """
    Deletes a customer from Firestore by mobile number.
    """
    if db is None:
        print("Error: Firestore not initialized in delete_customer.")
        return jsonify({"error": "Firestore not initialized"}), 500
    try:
        customer_ref = db.collection('customers').document(mobile_number)
        customer_doc = customer_ref.get()

        if not customer_doc.exists:
            print(f"Customer {mobile_number} not found for deletion.")
            return jsonify({"error": f"Customer {mobile_number} not found"}), 404

        customer_ref.delete()
        print(f"Customer {mobile_number} deleted successfully from Firestore.")
        return jsonify({"message": f"Customer {mobile_number} deleted successfully"}), 200
    except Exception as e:
        print(f"Error deleting customer {mobile_number}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/customers/import/analyze', methods=['POST'])
def analyze_customers_for_import():
    if db is None:
        print("Error: Firestore not initialized in analyze_customers_for_import.")
        return jsonify({"error": "Firestore not initialized"}), 500

    data = request.get_json()
    file_content_raw = data.get('file_content')
    file_format = data.get('file_format')

    if not file_content_raw:
        return jsonify({"error": "No file content provided"}), 400

    content_for_llm = ""
    if file_format == 'pdf':
        try:
            pdf_bytes = base64.b64decode(file_content_raw)
            reader = PdfReader(io.BytesIO(pdf_bytes))
            for page in reader.pages:
                content_for_llm += page.extract_text() + "\n"
            if not content_for_llm.strip():
                return jsonify({"error": "Could not extract text from PDF. It might be an image-based PDF or corrupted."}), 400
            print("Successfully extracted text from PDF for LLM analysis.")
        except Exception as e:
            print(f"Error extracting text from PDF: {e}")
            return jsonify({"error": f"Failed to process PDF file: {e}"}), 400
    else:
        content_for_llm = file_content_raw

    try:
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash-preview-05-20"
        )
        
        customer_schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "mobileNumber": {"type": "string", "nullable": True},
                    "name": {"type": "string"},
                    "address": {"type": "string", "nullable": True},
                    "email": {"type": "string", "nullable": True},
                    "taxId": {"type": "string", "nullable": True},
                    "taxNumber": {"type": "string", "nullable": True},
                },
                "required": ["name"]
            }
        }

        prompt = f"""
        You are an expert data mapper. Your task is to extract customer information from the provided text and format it as a JSON array of customer objects.
        Each customer object must adhere to the following JSON schema:
        {json.dumps(customer_schema, indent=2)}

        If a field is not present in the input text, it should be omitted or set to null.
        The 'name' field is required. If 'name' is missing for a record, discard that record.
        Combine address lines into a single string separated by newlines if multiple lines are implied.
        If a mobile number is not explicitly found, set 'mobileNumber' to null.

        Input data (original format: {file_format}):
        ---
        {content_for_llm}
        ---

        Please return ONLY the JSON array.
        """

        max_retries = 5
        llm_output_text = ""
        for i in range(max_retries):
            try:
                response = model.generate_content(
                    contents=[{"parts": [{"text": prompt}]}],
                    generation_config={
                        "response_mime_type": "application/json",
                        "response_schema": customer_schema
                    },
                    safety_settings={
                        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
                    },
                    request_options={"timeout": 300} # Add this line
                )
                
                if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                    llm_output_text = response.candidates[0].content.parts[0].text
                    break
                else:
                    raise ValueError("LLM response is empty or malformed.")
            except Exception as e:
                print(f"LLM call failed (attempt {i+1}/{max_retries}): {e}")
                if i < max_retries - 1:
                    time.sleep(2 ** i)
                else:
                    raise

        if not llm_output_text:
            return jsonify({"error": "AI mapping failed to produce output."}), 500

        mapped_customers_data = json.loads(llm_output_text)

        if not isinstance(mapped_customers_data, list):
            raise ValueError("LLM did not return a JSON array.")

        new_customers_count = 0
        updated_customers_count = 0
        analyzed_customers = []

        existing_customers_docs = db.collection('customers').stream()
        existing_customers_map = {doc.id: doc.to_dict() for doc in existing_customers_docs}

        for customer_data in mapped_customers_data:
            name = customer_data.get('name')
            mobile_number = customer_data.get('mobileNumber')
            
            if not name:
                print(f"Skipping record during analysis due to missing name: {customer_data}")
                continue

            doc_id_for_check = mobile_number if mobile_number else None 

            if doc_id_for_check and doc_id_for_check in existing_customers_map:
                customer_data['_status'] = 'updated'
                updated_customers_count += 1
            else:
                customer_data['_status'] = 'new'
                new_customers_count += 1
            
            analyzed_customers.append(customer_data)

        return jsonify({
            "status": "analysis_complete",
            "new_customers_count": new_customers_count,
            "updated_customers_count": updated_customers_count,
            "analyzed_data": analyzed_customers
        }), 200

    except json.JSONDecodeError as e:
        print(f"JSON Decode Error from LLM output: {e}")
        return jsonify({"error": f"AI mapping produced invalid JSON: {e}"}), 500
    except Exception as e:
        print(f"Error during customer import analysis: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/customers/import/confirm', methods=['POST'])
def confirm_import_customers():
    if db is None:
        print("Error: Firestore not initialized in confirm_import_customers.")
        return jsonify({"error": "Firestore not initialized"}), 500

    data = request.get_json()
    customers_to_save = data.get('customers_to_save')

    if not customers_to_save or not isinstance(customers_to_save, list):
        return jsonify({"error": "No customer data provided for confirmation."}), 400

    imported_count = 0
    for customer_data in customers_to_save:
        name = customer_data.get('name')
        mobile_number = customer_data.get('mobileNumber')

        if not name:
            print(f"Skipping record during save due to missing name: {customer_data}")
            continue

        doc_id = mobile_number if mobile_number else str(uuid.uuid4())
        is_generated_id = bool(not mobile_number)

        customer_ref = db.collection('customers').document(doc_id)
        customer_ref.set({
            'name': name,
            'mobileNumber': mobile_number,
            'address': customer_data.get('address'),
            'email': customer_data.get('email'),
            'taxId': customer_data.get('taxId'),
            'taxNumber': customer_data.get('taxNumber'),
            'isGeneratedId': is_generated_id,
            'lastUpdated': firestore.SERVER_TIMESTAMP
        }, merge=True)
        imported_count += 1

    return jsonify({"message": f"Successfully imported {imported_count} customers."}), 200

@app.route('/settings', methods=['GET', 'POST']) # MODIFIED: Allow POST requests
def get_settings():
    if db is None:
        print("Error: Firestore not initialized in get_settings.")
        return jsonify({"error": "Firestore not initialized"}), 500

    settings_doc_ref = db.collection('settings').document('company_profile')

    if request.method == 'GET':
        try:
            settings_doc = settings_doc_ref.get()
            if settings_doc.exists:
                settings_data = settings_doc.to_dict()
                print("Company settings fetched successfully.")
                return jsonify(settings_data), 200
            else:
                # Create a default document if it doesn't exist
                default_settings = {
                    "companyName": "NALAM FOODS USA",
                    "address": "123 Main St, Anytown, USA",
                    "registrationNumber": "",
                    "paymentTypes": ["Cash", "Credit Card"],
                    "themeName": "Green",
                    "email": "nalamfoodsllc@gmail.com",
                    "website": "nalamfoodsusa.com",
                    "taxPercentage": 0.0,
                    "shippingCost": 0.0,
                    "discountPercentage": 0.0,
                    "defaultInvoiceType": "Invoice",
                    "daysDue": 30
                }
                settings_doc_ref.set(default_settings)
                print("Company settings document not found. A default document was created.")
                return jsonify(default_settings), 200
        except Exception as e:
            print(f"Error fetching settings: {e}")
            return jsonify({"error": str(e)}), 500
    
    elif request.method == 'POST':
        try:
            settings_data = request.get_json()
            # You might want to validate the incoming settings_data here
            settings_doc_ref.set(settings_data, merge=True) # Use merge=True to update existing fields
            print("Company settings saved successfully.")
            return jsonify({"message": "Settings saved successfully"}), 200
        except Exception as e:
            print(f"Error saving settings: {e}")
            return jsonify({"error": str(e)}), 500

@app.route('/invoices/import/analyze', methods=['POST'])
def analyze_invoices_for_import():
    if db is None:
        print("Error: Firestore not initialized in analyze_invoices_for_import.")
        return jsonify({"error": "Firestore not initialized"}), 500

    data = request.get_json()
    file_content_raw = data.get('file_content')
    file_format = data.get('file_format')

    if not file_content_raw:
        return jsonify({"error": "No file content provided"}), 400

    content_for_llm = ""
    if file_format == 'pdf':
        try:
            pdf_bytes = base64.b64decode(file_content_raw)
            reader = PdfReader(io.BytesIO(pdf_bytes))
            for page in reader.pages:
                content_for_llm += page.extract_text() + "\n"
            if not content_for_llm.strip():
                return jsonify({"error": "Could not extract text from PDF. It might be an image-based PDF or corrupted."}), 400
            print("Successfully extracted text from PDF for LLM analysis.")
        except Exception as e:
            print(f"Error extracting text from PDF: {e}")
            return jsonify({"error": f"Failed to process PDF file: {e}"}), 400
    else:
        content_for_llm = file_content_raw

    try:
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash-preview-05-20"
        )
        
        # Define the desired JSON schema for invoice and customer data
        # Each item in the array will represent an invoice, and can optionally include customer details
        invoice_and_customer_schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "invoice": {
                        "type": "object",
                        "properties": {
                            "invoiceNumber": {"type": "string", "nullable": True}, # Can be null for new invoices
                            "billToName": {"type": "string"},
                            "billToAddress": {"type": "string", "nullable": True},
                            "mobileNumber": {"type": "string", "nullable": True},
                            "paymentType": {"type": "string", "nullable": True},
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "productId": {"type": "string", "nullable": True},
                                        "name": {"type": "string"},
                                        "price": {"type": "number"},
                                        "quantity": {"type": "integer"},
                                        "subtotal": {"type": "number"},
                                    },
                                    "required": ["name", "price", "quantity", "subtotal"]
                                }
                            },
                            "totalAmount": {"type": "number"},
                            "invoiceDate": {"type": "string"}, # ISO format
                            "invoiceTaxPercentage": {"type": "number", "nullable": True},
                            "invoiceShippingCost": {"type": "number", "nullable": True},
                            "invoiceDiscountPercentage": {"type": "number", "nullable": True},
                            "status": {"type": "string", "nullable": True},
                            "dueDate": {"type": "string", "nullable": True}, # ISO format
                            "totalPaid": {"type": "number", "nullable": True},
                            "payments": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "amount": {"type": "number"},
                                        "date": {"type": "string"}, # ISO format
                                        "type": {"type": "string", "nullable": True}
                                    },
                                    "required": ["amount", "date"]
                                }
                            },
                            "daysDue": {"type": "integer", "nullable": True},
                            "invoiceType": {"type": "string", "nullable": True},
                        },
                        "required": ["billToName", "items", "totalAmount", "invoiceDate"]
                    },
                    "customer": { # Extracted customer details, potentially for saving
                        "type": "object",
                        "properties": {
                            "mobileNumber": {"type": "string", "nullable": True},
                            "name": {"type": "string"},
                            "address": {"type": "string", "nullable": True},
                            "email": {"type": "string", "nullable": True},
                            "taxId": {"type": "string", "nullable": True},
                            "taxNumber": {"type": "string", "nullable": True},
                        },
                        "required": ["name"]
                    }
                },
                "required": ["invoice", "customer"] # Each analyzed item must have an invoice and customer part
            }
        }

        prompt = f"""
        You are an expert data mapper for invoices and customers. Your task is to extract invoice and associated customer information from the provided text and format it as a JSON array.
        Each item in the array must be an object containing an 'invoice' object and a 'customer' object, adhering to the following JSON schema:
        {json.dumps(invoice_and_customer_schema, indent=2)}

        For the 'invoice' object:
        - 'invoiceNumber' can be null if not explicitly found.
        - 'invoiceDate' and 'dueDate' must be in ISO 8601 format (e.g., "YYYY-MM-DDTHH:MM:SS"). If time is not specified, use "00:00:00". If date is not specified, use today's date.
        - 'totalAmount' should be the final total after all calculations.
        - 'items' should be a list of product items. If subtotal is not explicit, calculate it as price * quantity.
        - 'payments' should be a list of payment records. 'date' in payments must also be ISO 8601.
        - Set numeric fields (tax, shipping, discount, totalPaid) to 0.0 if not found.
        - Set 'status' to 'Unpaid' if not found.
        - Set 'invoiceType' to 'Invoice' if not found.
        - Set 'daysDue' to 1 if not found.

        For the 'customer' object:
        - 'mobileNumber' can be null if not explicitly found.
        - 'name' is required. If 'name' is missing for a customer, discard that customer record.
        - Combine address lines into a single string separated by newlines if multiple lines are implied.

        If a field is not present in the input text, it should be omitted or set to null, unless a default is specified above.
        If an invoice record cannot be fully parsed (e.g., missing billToName, items, totalAmount, or invoiceDate), discard that entire invoice record.

        Input data (original format: {file_format}):
        ---
        {content_for_llm}
        ---

        Please return ONLY the JSON array.
        """

        max_retries = 5
        llm_output_text = ""
        for i in range(max_retries):
            try:
                response = model.generate_content(
                    contents=[{"parts": [{"text": prompt}]}],
                    generation_config={
                        "response_mime_type": "application/json",
                        "response_schema": invoice_and_customer_schema
                    },
                    safety_settings={
                        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
                    },
                    request_options={"timeout": 300} # Add this line
                )
                
                if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                    llm_output_text = response.candidates[0].content.parts[0].text
                    break
                else:
                    raise ValueError("LLM response is empty or malformed.")
            except Exception as e:
                print(f"LLM call failed (attempt {i+1}/{max_retries}): {e}")
                if i < max_retries - 1:
                    time.sleep(2 ** i)
                else:
                    raise

        if not llm_output_text:
            return jsonify({"error": "AI mapping failed to produce output."}), 500

        mapped_data = json.loads(llm_output_text)

        if not isinstance(mapped_data, list):
            raise ValueError("LLM did not return a JSON array.")

        new_customers_count = 0
        updated_customers_count = 0
        new_invoices_count = 0
        updated_invoices_count = 0
        analyzed_results = [] # Stores {invoice: {...}, customer: {...}} with _status flags

        # Fetch existing customers and invoices for comparison
        existing_customers_docs = db.collection('customers').stream()
        existing_customers_map = {doc.id: doc.to_dict() for doc in existing_customers_docs}

        existing_invoices_docs = db.collection('invoices').stream()
        existing_invoices_map = {doc.id: doc.to_dict() for doc in existing_invoices_docs}


        for item in mapped_data:
            invoice_data = item.get('invoice')
            customer_data = item.get('customer')

            if not invoice_data or not customer_data:
                print(f"Skipping record due to missing invoice or customer data: {item}")
                continue

            # --- Analyze Customer ---
            customer_mobile_number = customer_data.get('mobileNumber')
            customer_name = customer_data.get('name')

            if not customer_name:
                print(f"Skipping customer analysis for record due to missing name: {customer_data}")
                customer_data['_status'] = 'skipped' # Mark customer as skipped
            else:
                customer_doc_id_for_check = customer_mobile_number if customer_mobile_number else None
                if customer_doc_id_for_check and customer_doc_id_for_check in existing_customers_map:
                    customer_data['_status'] = 'updated'
                    updated_customers_count += 1
                else:
                    customer_data['_status'] = 'new'
                    new_customers_count += 1
            
            # --- Analyze Invoice ---
            invoice_number = invoice_data.get('invoiceNumber')
            
            if not invoice_data.get('billToName') or not invoice_data.get('items') or not invoice_data.get('totalAmount') or not invoice_data.get('invoiceDate'):
                print(f"Skipping invoice analysis for record due to missing required fields: {invoice_data}")
                invoice_data['_status'] = 'skipped' # Mark invoice as skipped
            else:
                # Ensure dates are in correct format for comparison
                try:
                    invoice_date_str = invoice_data['invoiceDate']
                    datetime.datetime.fromisoformat(invoice_date_str.replace('Z', '+00:00'))
                except ValueError:
                    invoice_data['invoiceDate'] = datetime.datetime.now().isoformat()
                    print(f"Corrected invoiceDate format for {invoice_number}")
                
                if invoice_data.get('dueDate'):
                    try:
                        due_date_str = invoice_data['dueDate']
                        datetime.datetime.fromisoformat(due_date_str.replace('Z', '+00:00'))
                    except ValueError:
                        invoice_data['dueDate'] = (datetime.datetime.now() + datetime.timedelta(days=invoice_data.get('daysDue', 1))).isoformat()
                        print(f"Corrected dueDate format for {invoice_number}")
                else:
                    invoice_data['dueDate'] = (datetime.datetime.now() + datetime.timedelta(days=invoice_data.get('daysDue', 1))).isoformat()


                if invoice_number and invoice_number in existing_invoices_map:
                    invoice_data['_status'] = 'updated'
                    updated_invoices_count += 1
                else:
                    invoice_data['_status'] = 'new'
                    new_invoices_count += 1
            
            analyzed_results.append({'invoice': invoice_data, 'customer': customer_data})

        return jsonify({
            "status": "analysis_complete",
            "new_customers_count": new_customers_count,
            "updated_customers_count": updated_customers_count,
            "new_invoices_count": new_invoices_count,
            "updated_invoices_count": updated_invoices_count,
            "analyzed_data": analyzed_results
        }), 200

    except json.JSONDecodeError as e:
        print(f"JSON Decode Error from LLM output: {e}")
        return jsonify({"error": f"AI mapping produced invalid JSON: {e}"}), 500
    except Exception as e:
        print(f"Error during invoice import analysis: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/invoices/import/confirm', methods=['POST'])
def confirm_import_invoices():
    if db is None:
        print("Error: Firestore not initialized in confirm_import_invoices.")
        return jsonify({"error": "Firestore not initialized"}), 500

    data = request.get_json()
    invoices_to_save = data.get('invoices_to_save')

    if not invoices_to_save or not isinstance(invoices_to_save, list):
        return jsonify({"error": "No invoice data provided for confirmation."}), 400

    imported_invoices_count = 0
    for item in invoices_to_save:
        invoice_data = item.get('invoice')
        
        if not invoice_data or invoice_data.get('_status') == 'skipped':
            continue

        if invoice_data.get('_status') == 'new':
            invoice_date_obj = datetime.datetime.now()
            if 'invoiceDate' in invoice_data and isinstance(invoice_data['invoiceDate'], str):
                try:
                    invoice_date_obj = datetime.datetime.fromisoformat(invoice_data['invoiceDate'].replace('Z', '+00:00'))
                except ValueError:
                    print(f"Could not parse invoiceDate string {invoice_data['invoiceDate']}. Using current date for invoice number generation.")
            
            new_invoice_number = generate_unique_invoice_number(invoice_date_obj)
            invoice_data['invoiceNumber'] = new_invoice_number
            invoice_data['invoiceDatePrefix'] = invoice_date_obj.strftime("%Y%m%d")

        if 'invoiceDate' in invoice_data:
            if isinstance(invoice_data['invoiceDate'], str):
                try:
                    invoice_data['invoiceDate'] = datetime.datetime.fromisoformat(invoice_data['invoiceDate'].replace('Z', '+00:00'))
                except ValueError:
                    invoice_data['invoiceDate'] = datetime.datetime.now()
            if isinstance(invoice_data['invoiceDate'], datetime.datetime):
                invoice_data['invoiceDate'] = invoice_data['invoiceDate'].isoformat()
        else:
            invoice_data['invoiceDate'] = datetime.datetime.now().isoformat()

        if 'dueDate' in invoice_data:
            if isinstance(invoice_data['dueDate'], str):
                try:
                    invoice_data['dueDate'] = datetime.datetime.fromisoformat(invoice_data['dueDate'].replace('Z', '+00:00'))
                except ValueError:
                    invoice_data['dueDate'] = datetime.datetime.now()
            if isinstance(invoice_data['dueDate'], datetime.datetime):
                invoice_data['dueDate'] = invoice_data['dueDate'].isoformat()
        else:
            invoice_data['dueDate'] = (datetime.datetime.now() + datetime.timedelta(days=invoice_data.get('daysDue', 1))).isoformat()

        for key in ['totalAmount', 'invoiceTaxPercentage', 'invoiceShippingCost', 'invoiceDiscountPercentage', 'totalPaid']:
            if key in invoice_data and not isinstance(invoice_data[key], (int, float)):
                try:
                    invoice_data[key] = float(invoice_data[key])
                except (ValueError, TypeError):
                    invoice_data[key] = 0.0

        if 'payments' in invoice_data and isinstance(invoice_data['payments'], list):
            for payment in invoice_data['payments']:
                if 'date' in payment:
                    if isinstance(payment['date'], str):
                        try:
                            payment['date'] = datetime.datetime.fromisoformat(payment['date'].replace('Z', '+00:00'))
                        except ValueError:
                            payment['date'] = datetime.datetime.now()
                    if isinstance(payment['date'], datetime.datetime):
                        payment['date'] = payment['date'].isoformat()
                else:
                    payment['date'] = datetime.datetime.now().isoformat()

                if 'amount' in payment and not isinstance(payment['amount'], (int, float)):
                    try:
                        payment['amount'] = float(payment['amount'])
                    except (ValueError, TypeError):
                        payment['amount'] = 0.0

        invoice_data.pop('_status', None)
        
        invoice_ref = db.collection('invoices').document(invoice_data['invoiceNumber'])
        invoice_ref.set(invoice_data, merge=True)
        imported_invoices_count += 1
    
    return jsonify({"message": f"Successfully imported {imported_invoices_count} invoices."}), 200


def generate_unique_invoice_number(invoice_date_obj):
    if db is None:
        # It's good practice to raise an exception if db is not initialized
        raise Exception("Firestore not initialized.")
    
    today_str = invoice_date_obj.strftime("%Y%m%d")
    invoices_today_docs = db.collection('invoices').where(filter=firestore.FieldFilter('invoiceDatePrefix', '==', today_str)).get()
    count_today = len(invoices_today_docs)
    invoice_suffix = str(count_today + 1).zfill(3)
    return f"{today_str}{invoice_suffix}"



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)



