from flask import Flask, render_template, request
import json
import os
import requests
from werkzeug.utils import secure_filename

app = Flask(__name__)


@app.route("/")
def hello_world():
    return render_template("index.html", title="Hello")


@app.route("/add_invoice", methods=['GET', 'POST'])
def add_invoice():
    if request.method == 'POST':
        if 'json_file' in request.files:  # Check if a file was uploaded
            file = request.files['json_file']
            if file.filename == '':
                return 'No selected file'
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_path = os.path.join('data/invoices/singles', filename)
                file.save(file_path)
                return 'Invoice added from file successfully'
        else:  # If no file was uploaded, add invoice manually
            invoice_data = {
                'invoice_number': request.form.get('invoice_number'),
                'invoice_quote': request.form.get('invoice_quote'),
                'invoice_date_issue': request.form.get('invoice_date_issue'),
                'currency': request.form.get('currency'),
                'status': False,
                'payments': []
            }
            os.makedirs('data/invoices/singles', exist_ok=True)
            with open(f'data/invoices/singles/{invoice_data["invoice_number"]}.json', 'w') as f:
                json.dump(invoice_data, f)
            # Oblicz kurs waluty dla daty wystawienia faktury
            get_exchange_rates_nbp(invoice_data["invoice_number"], invoice_data["currency"])
            return 'Invoice added manually successfully'
    return render_template("add_invoice.html")


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() == 'json'


@app.route("/invoices", methods=['GET'])
def invoices():
    invoice_number = request.args.get('invoice_number')
    invoices = []
    for filename in os.listdir('data/invoices/singles'):
        with open(f'data/invoices/singles/{filename}') as f:
            invoice = json.load(f)
            if invoice_number is None or invoice['invoice_number'] == invoice_number:
                invoices.append(invoice)
    return render_template("invoices.html", invoices=invoices)


def get_invoices(invoice_number=None):
    invoices = []
    for filename in os.listdir('data/invoices/singles'):
        invoice = load_json(f'data/invoices/singles/{filename}')
        if invoice['status'] is False and (
                invoice_number is None or invoice['invoice_number'] == invoice_number):
            invoices.append(invoice)
    return invoices


def add_payment_to_invoice(invoice_number, payment_amount, payment_date):
    invoice = load_json(f'data/invoices/singles/{invoice_number}.json')
    if invoice['status'] is False:
        total_payments = sum(float(payment['amount']) for payment in invoice['payments'])
        if total_payments >= float(invoice['invoice_quote']):
            return 'Invoice is already paid', 400
        elif total_payments + float(payment_amount) > float(invoice['invoice_quote']):
            return 'Payment amount exceeds invoice value', 400
        else:
            payment = {
                'amount': payment_amount,
                'date': payment_date
            }
            invoice['payments'].append(payment)
            total_payments += float(payment_amount)
            if total_payments >= float(invoice['invoice_quote']):
                invoice['status'] = True
            with open(f'data/invoices/singles/{invoice_number}.json', 'w') as f:
                json.dump(invoice, f)
            # Oblicz kurs waluty dla daty płatności
            get_exchange_rates_nbp(invoice_number, invoice["currency"])
            return 'Payment added'
    else:
        return 'Invoice is already paid, or payment exceeds the quote', 400


def get_invoice_issue_exchange_rates_nbp(invoice_number):
    invoice = load_json(f'data/invoices/singles/{invoice_number}.json')
    return get_exchange_rates_nbp(invoice['invoice_date_issue'])


def get_payment_date_exchange_rates_nbp(invoice_number, payment_index):
    invoice = load_json(f'data/invoices/singles/{invoice_number}.json')
    return get_exchange_rates_nbp(invoice['payments'][payment_index]['date'])


@app.route("/add_payment", methods=['GET', 'POST'])
def add_payment():
    invoice_number = request.args.get('invoice_number')
    invoices = get_invoices(invoice_number)
    if request.method == 'POST':
        invoice_number = request.form.get('invoice_number')
        payment_amount = request.form.get('payment_amount')
        payment_date = request.form.get('payment_date')
        result = add_payment_to_invoice(invoice_number, payment_amount, payment_date)
        if isinstance(result, tuple) and result[1] == 400:
            return result[0], 400
        else:
            return result
    return render_template("add_payment.html", invoices=invoices)


def load_json(filepath):
    with open(filepath, 'r') as f:
        data = json.load(f)
    return data


def get_exchange_rates_nbp(invoice_number, currency):
    invoice = load_json(f'data/invoices/singles/{invoice_number}.json')
    dates = [invoice['invoice_date_issue']] + [payment['date'] for payment in invoice['payments']]
    exchange_rates = {}
    # Wczytaj dane z pliku cache.json, jeśli istnieje
    cache = []
    if os.path.exists('cache.json'):
        with open('cache.json', 'r') as f:
            cache = json.load(f)
    for date in dates:
        # Sprawdź, czy dla danej waluty i daty istnieje już wpis w cache
        cached_rate = next((item for item in cache if item["date"] == date and item["currency"] == currency), None)
        if cached_rate is not None:
            # Jeśli istnieje, użyj go
            exchange_rates[date] = cached_rate['rate']
        else:
            # W przeciwnym razie, pobierz kurs z API
            response = requests.get(f'http://api.nbp.pl/api/exchangerates/rates/A/{currency}/{date}/?format=json')
            response.raise_for_status()  # Sprawdź, czy odpowiedź jest poprawna
            rate = response.json()['rates'][0]['mid']
            exchange_rates[date] = rate
            # Dodaj nowy wpis do cache
            cache.append({"currency": currency, "date": date, "rate": rate})
    # Zapisz cache do pliku
    with open('cache.json', 'w') as f:
        json.dump(cache, f)
    return exchange_rates


@app.context_processor
def utility_functions():
    def get_cached_exchange_rates(invoice_number, currency):
        # Load the cache
        with open('cache.json', 'r') as f:
            cache = json.load(f)
        # Filter the cache for the given invoice number and currency
        rates = {item['date']: float(item['rate']) for item in cache if item['currency'] == currency}
        return rates

    def calculate_exchange_rate_difference(invoice_number):
        # Load the invoice data
        invoice = load_json(f'data/invoices/singles/{invoice_number}.json')

        # Get the exchange rate on the invoice issue date
        issue_date_rate = get_cached_exchange_rates(invoice_number, invoice['currency'])[invoice['invoice_date_issue']]

        # Calculate the total payment amount in the invoice currency
        total_payment_in_invoice_currency = sum(
            float(payment['amount']) * get_cached_exchange_rates(invoice_number, invoice['currency'])[payment['date']]
            for
            payment in invoice['payments'])

        # Calculate the difference between the invoice quote in the invoice currency and the total payment amount in the invoice currency
        exchange_rate_difference = float(invoice['invoice_quote']) * issue_date_rate - total_payment_in_invoice_currency

        # Round the exchange rate difference to 2 decimal places
        exchange_rate_difference = round(exchange_rate_difference, 2)

        return exchange_rate_difference

    return dict(get_cached_exchange_rates=get_cached_exchange_rates,
                calculate_exchange_rate_difference=calculate_exchange_rate_difference)


def get_cached_exchange_rates(invoice_number, currency):
    # Load the cache
    with open('cache.json', 'r') as f:
        cache = json.load(f)
    # Filter the cache for the given invoice number and currency
    rates = {item['date']: item['rate'] for item in cache if item['currency'] == currency}
    return rates
