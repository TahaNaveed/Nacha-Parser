from flask import Flask, render_template, request, flash, redirect, url_for
from collections import defaultdict

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

def determine_transaction_type(transaction_code):
    """Determine if transaction is debit/credit and Bank-to-Card/Card-to-Bank/Direct Deposit"""
    transaction_code = str(transaction_code)
    
    # Determine debit/credit
    if transaction_code in ['22', '23', '24', '27', '28', '29', '32', '33', '34', '37', '38', '39']:
        transaction_type = 'Debit'
    elif transaction_code in ['21', '26', '31', '36']:
        transaction_type = 'Credit'
    else:
        transaction_type = 'Unknown'
    
    # Determine transaction class
    if transaction_code in ['22', '23', '24', '27', '28', '29']:
        entry_class = 'Bank to Card' if transaction_type == 'Credit' else 'Card to Bank'
    elif transaction_code in ['32', '33', '34', '37', '38', '39']:
        entry_class = 'Card to Bank' if transaction_type == 'Debit' else 'Bank to Card'
    elif transaction_code in ['21', '26', '31', '36']:
        entry_class = 'Direct Deposit'
    else:
        entry_class = 'Unknown'
    
    return transaction_type, entry_class

def parse_nacha_grouped(content):
    file_header = None
    file_control = None
    batches = []
    current_batch = None
    
    # Initialize totals for file control
    total_debit_amount = 0
    total_credit_amount = 0
    total_entry_count = 0

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue

        record_type = line[0]

        if record_type == '1':  # File Header
            file_header = {
                'Priority Code': line[1:3],
                'Immediate Destination': line[3:13].strip(),
                'Immediate Origin': line[13:23].strip(),
                'File Creation Date': line[23:29],
                'File Creation Time': line[29:32],
                'File ID Modifier': line[32:33],
                'Record Size': line[33:36],
                'Blocking Factor': line[36:39],
                'Format Code': line[39:40],
                'Immediate Destination Name': line[40:63].strip(),
                'Immediate Origin Name': line[63:86].strip(),
                'Reference Code': line[86:94].strip()
            }

        elif record_type == '5':  # Batch Header
            current_batch = {
                'Batch Header': {
                    'Service Class Code': line[1:4],
                    'Company Name': line[4:20].strip(),
                    'Company Discretionary Data': line[20:40].strip(),
                    'Company Identification': line[40:50].strip(),
                    'Standard Entry Class Code': line[50:53],
                    'Company Entry Description': line[53:63].strip(),
                    'Company Descriptive Date': line[63:69],
                    'Effective Entry Date': line[69:75],
                    'Settlement Date': line[75:78],
                    'Originator Status Code': line[78:79],
                    'Originating DFI Identification': line[79:87],
                    'Batch Number': line[87:94]
                },
                'Entry Details': [],
                'Batch Control': {}
            }
            batches.append(current_batch)

        elif record_type == '6' and current_batch:  # Entry Detail
            transaction_code = line[1:3]
            amount = int(line[29:39]) / 100.0
            transaction_type, entry_class = determine_transaction_type(transaction_code)
            
            # Update file-level totals
            if transaction_type == 'Debit':
                total_debit_amount += amount
            else:
                total_credit_amount += amount
            total_entry_count += 1
            
            entry = {
                'Transaction Code': transaction_code,
                'Transaction Type': transaction_type,
                'Entry Class': entry_class,
                'Receiving DFI Identification': line[3:11],
                'Check Digit': line[11:12],
                'DFI Account Number': line[12:29].strip(),
                'Amount': "{:.2f}".format(abs(amount)),
                'Individual Identification Number': line[39:54].strip(),
                'Individual Name': line[54:76].strip(),
                'Discretionary Data': line[76:78].strip(),
                'Addenda Record Indicator': line[78:79],
                'Trace Number': line[79:94].strip(),
                'Raw Amount': amount
            }
            current_batch['Entry Details'].append(entry)

        elif record_type == '8' and current_batch:  # Batch Control
            current_batch['Batch Control'] = {
                'Service Class Code': line[1:4],
                'Entry/Addenda Count': line[4:10],
                'Entry Hash': line[10:20],
                'Total Debit Entry Dollar Amount': "{:.2f}".format(abs(int(line[20:32])) / 100.0),
                'Total Credit Entry Dollar Amount': "{:.2f}".format(abs(int(line[32:44])) / 100.0),
                'Company Identification': line[44:54].strip(),
                'Message Authentication Code': line[54:73].strip(),
                'Reserved': line[73:79].strip(),
                'Originating DFI Identification': line[79:87],
                'Batch Number': line[87:94]
            }

        elif record_type == '9':  # File Control
            # Use our calculated totals instead of the values from the file
            file_control = {
                'Batch Count': line[1:7],
                'Block Count': line[7:13],
                'Entry/Addenda Count': line[13:21],
                'Entry Hash': line[21:31],
                'Total Debit Entry Dollar Amount': "{:.2f}".format(total_debit_amount),
                'Total Credit Entry Dollar Amount': "{:.2f}".format(total_credit_amount),
                'Reserved': line[55:94].strip()
            }

    return {
        'File Header': file_header,
        'Batches': batches,
        'File Control': file_control
    }

@app.route("/", methods=["GET", "POST"])
def index():
    data = None
    if request.method == "POST":
        if 'nacha_file' not in request.files:
            flash('No file selected', 'error')
            return redirect(request.url)
            
        file = request.files['nacha_file']
        
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(request.url)
            
        if file and file.filename.endswith('.txt'):
            try:
                content = file.read().decode("utf-8")
                data = parse_nacha_grouped(content)
                if not data or not data.get('File Header'):
                    flash('The file does not appear to be a valid NACHA file', 'error')
                else:
                    flash('File successfully parsed!', 'success')
            except UnicodeDecodeError:
                flash('File could not be decoded as UTF-8 text', 'error')
            except Exception as e:
                flash(f'Error processing file: {str(e)}', 'error')
        else:
            flash('Please upload a .txt file', 'error')
            
    return render_template("index.html", data=data)

if __name__ == "__main__":
    app.run(debug=True)
