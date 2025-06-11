from flask import Flask, render_template, request
from collections import defaultdict

app = Flask(__name__)

def parse_nacha(content):
    parsed = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue

        record_type = line[0]
        record = {}

        if record_type == '1':
            record = {
                'Record Type': 'File Header',
                'Immediate Destination': line[3:13].strip(),
                'Immediate Origin': line[13:23].strip(),
                'File ID Modifier': line[39:40],
                'File Creation Date': line[23:29],
                'Format Code': line[38:39]
            }
        elif record_type == '5':
            record = {
                'Record Type': 'Batch Header',
                'Service Class Code': line[1:4],
                'Company Name': line[4:20].strip(),
                'Company ID': line[40:50].strip(),
                'Standard Entry Class': line[50:53],
                'Entry Description': line[53:63].strip(),
                'Effective Entry Date': line[69:75],
            }
        elif record_type == '6':
            record = {
                'Record Type': 'Entry Detail',
                'Transaction Code': line[1:3],
                'Routing Number': line[3:11],
                'Check Digit': line[11],
                'Account Number': line[12:29].strip(),
                'Amount': int(line[29:39]) / 100.0,
                'Individual ID': line[39:54].strip(),
                'Individual Name': line[54:76].strip(),
                'Trace Number': line[79:94].strip()
            }
        elif record_type == '8':
            record = {
                'Record Type': 'Batch Control',
                'Entry/Addenda Count': line[4:10],
                'Entry Hash': line[10:20],
                'Total Debit ($)': int(line[20:32]) / 100.0,
                'Total Credit ($)': int(line[32:44]) / 100.0
            }
        elif record_type == '9':
            record = {
                'Record Type': 'File Control',
                'Batch Count': line[1:7],
                'Block Count': line[7:13],
                'Entry/Addenda Count': line[13:21],
                'Total Debit ($)': int(line[33:45]) / 100.0,
                'Total Credit ($)': int(line[45:57]) / 100.0
            }

        if record:
            parsed.append(record)
    return parsed

def parse_nacha_grouped(content):
    file_header = None
    file_control = None
    batches = []
    current_batch = None

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue

        record_type = line[0]

        if record_type == '1':
            file_header = {
                'Immediate Destination': line[3:13].strip(),
                'Immediate Origin': line[13:23].strip(),
                'File ID Modifier': line[39:40],
                'File Creation Date': line[23:29],
                'Format Code': line[38:39]
            }

        elif record_type == '5':  # Start of new batch
            current_batch = {
                'Batch Header': {
                    'Service Class Code': line[1:4],
                    'Company Name': line[4:20].strip(),
                    'Company ID': line[40:50].strip(),
                    'Standard Entry Class': line[50:53],
                    'Entry Description': line[53:63].strip(),
                    'Effective Entry Date': line[69:75],
                },
                'Entry Details': [],
                'Batch Control': {}
            }
            batches.append(current_batch)

        elif record_type == '6' and current_batch:
            entry = {
                'Transaction Code': line[1:3],
                'Routing Number': line[3:11],
                'Check Digit': line[11],
                'Account Number': line[12:29].strip(),
                'Amount': int(line[29:39]) / 100.0,
                'Individual ID': line[39:54].strip(),
                'Individual Name': line[54:76].strip(),
                'Trace Number': line[79:94].strip()
            }
            current_batch['Entry Details'].append(entry)

        elif record_type == '8' and current_batch:
            current_batch['Batch Control'] = {
                'Entry/Addenda Count': line[4:10],
                'Entry Hash': line[10:20],
                'Total Debit ($)': int(line[20:32]) / 100.0,
                'Total Credit ($)': int(line[32:44]) / 100.0
            }

        elif record_type == '9':
            file_control = {
                'Batch Count': line[1:7],
                'Block Count': line[7:13],
                'Entry/Addenda Count': line[13:21],
                'Total Debit ($)': int(line[33:45]) / 100.0,
                'Total Credit ($)': int(line[45:57]) / 100.0
            }

    return {
        'File Header': file_header,
        'Batches': batches,
        'File Control': file_control
    }

def group_records(records):
    grouped = defaultdict(list)
    for r in records:
        group = r.get("Record Type", "Unknown")
        grouped[group].append(r)
    return grouped

@app.route("/", methods=["GET", "POST"])
def index():
    data = None
    if request.method == "POST":
        file = request.files.get("nacha_file")
        if file:
            content = file.read().decode("utf-8")
            data = parse_nacha_grouped(content)
    return render_template("index.html", data=data)


if __name__ == "__main__":
    app.run(debug=True)
