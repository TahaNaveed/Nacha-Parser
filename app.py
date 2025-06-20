from flask import Flask, render_template, request, flash, redirect, url_for, send_file
from collections import defaultdict
from io import BytesIO
import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'your_super_secret_key_here_replace_in_prod_12345'

NACHA_RECORD_LENGTH = 94

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

def parse_nacha_grouped(nacha_content):
    """Parses NACHA file content and groups records by type and batch."""
    logger.debug("Starting NACHA file parsing")
    lines = nacha_content.splitlines()
    data = defaultdict(list)
    current_batch = None
    batch_index = -1
    current_batch_entries = []

    # Initialize totals for file control
    total_debit_amount = 0
    total_credit_amount = 0
    total_entry_count = 0

    for i, line in enumerate(lines):
        if not line or len(line.strip()) < 2:
            logger.debug(f"Skipping empty/short line {i+1}")
            continue

        if len(line) < NACHA_RECORD_LENGTH:
            logger.warning(f"Line {i+1} is too short ({len(line)} chars)")
        elif len(line) > NACHA_RECORD_LENGTH:
            logger.warning(f"Line {i+1} is too long ({len(line)} chars)")
            line = line[:NACHA_RECORD_LENGTH]

        record_type = line[0]

        try:
            if record_type == '1':
                logger.debug("Found File Header record")
                data['File Header'] = {
                    'record_type_code': line[0],
                    'priority_code': line[1:3],
                    'immediate_destination': line[3:13].strip(),
                    'immediate_origin': line[13:23].strip(),
                    'file_creation_date': line[23:29],
                    'file_creation_time': line[29:33],
                    'file_id_modifier': line[33],
                    'record_size': line[34:37],
                    'blocking_factor': line[37:40],
                    'format_code': line[40],
                    'immediate_destination_name': line[41:64].strip(),
                    'immediate_origin_name': line[64:87].strip(),
                    'reference_code': line[87:95].strip()
                }

            elif record_type == '5':
                logger.debug(f"Found Batch Header record (Batch {batch_index + 1})")
                if current_batch and current_batch_entries:
                    current_batch['Entries'] = current_batch_entries
                    data['Batches'].append(current_batch)

                batch_index += 1
                current_batch = {
                    'Batch Header': {
                        'record_type_code': line[0],
                        'service_class_code': line[1:4],
                        'company_name': line[4:20].strip(),
                        'company_discretionary_data': line[20:40].strip(),
                        'company_identification': line[40:50].strip(),
                        'standard_entry_class_code': line[50:53],
                        'company_entry_description': line[53:63].strip(),
                        'descriptive_date': line[63:69],
                        'effective_entry_date': line[69:75],
                        'settlement_date': line[75:78],
                        'originator_status_code': line[78],
                        'originating_dfi_identification': line[79:87],
                        'batch_number': line[87:94]
                    },
                    'Entries': [],
                    'Batch Control': {}
                }
                current_batch_entries = []

            elif record_type == '6':
                logger.debug(f"Found Entry Detail record in batch {batch_index}")
                transaction_code = line[1:3]
                amount = int(line[29:39]) / 100.0
                transaction_type, entry_class = determine_transaction_type(transaction_code)

                # Update file-level totals
                if transaction_type == 'Debit':
                    total_debit_amount += amount
                else:
                    total_credit_amount += amount
                total_entry_count += 1

                entry_detail = {
                    'record_type_code': line[0],
                    'transaction_code': transaction_code,
                    'transaction_type': transaction_type,
                    'entry_class': entry_class,
                    'receiving_dfi_identification': line[3:11],
                    'check_digit': line[11],
                    'dfi_account_number': line[12:29].strip(),
                    'amount': "{:.2f}".format(abs(amount)),
                    'individual_identification_number': line[39:54].strip(),
                    'individual_name': line[54:76].strip(),
                    'discretionary_data': line[76:78].strip(),
                    'addenda_record_indicator': line[78],
                    'trace_number': line[79:94].strip(),
                    'raw_amount': amount
                }
                current_batch_entries.append(entry_detail)

            elif record_type == '7':
                logger.debug("Found Addenda record")
                if current_batch_entries:
                    addenda_detail = {
                        'record_type_code': line[0],
                        'type_code': line[1:3],
                        'payment_related_info': line[3:83].strip(),
                        'addenda_sequence_number': line[83:87],
                        'entry_detail_sequence_number': line[87:94]
                    }
                    if 'Addenda' not in current_batch_entries[-1]:
                        current_batch_entries[-1]['Addenda'] = []
                    current_batch_entries[-1]['Addenda'].append(addenda_detail)
                else:
                    logger.warning(f"Addenda record without preceding Entry Detail at line {i+1}")
                    flash(f"Warning: Addenda record (line {i+1}) found without a preceding Entry Detail record. Skipping.", 'warning')

            elif record_type == '8':
                logger.debug(f"Found Batch Control record for batch {batch_index}")
                if current_batch:
                    current_batch['Batch Control'] = {
                        'record_type_code': line[0],
                        'service_class_code': line[1:4],
                        'entry_addenda_count': line[4:10],
                        'entry_hash': line[10:20],
                        'total_debit_amount': "{:.2f}".format(abs(int(line[20:32])) / 100.0),
                        'total_credit_amount': "{:.2f}".format(abs(int(line[32:44])) / 100.0),
                        'company_identification': line[44:54].strip(),
                        'message_authentication_code': line[54:73].strip(),
                        'reserved': line[73:79].strip(),
                        'originating_dfi_identification': line[79:87],
                        'batch_number': line[87:94]
                    }
                    current_batch['Entries'] = current_batch_entries
                    data['Batches'].append(current_batch)
                    current_batch = None
                    current_batch_entries = []
                else:
                    logger.warning(f"Batch Control without Batch Header at line {i+1}")
                    flash(f"Warning: Batch Control record (line {i+1}) found without a preceding Batch Header record. Skipping.", 'warning')

            elif record_type == '9':
                logger.debug("Found File Control record")
                if line.strip() == '9' * NACHA_RECORD_LENGTH:
                    data['File Padding Count'] = data.get('File Padding Count', 0) + 1
                    continue

                # Use our calculated totals instead of the values from the file
                data['File Control'] = {
                    'record_type_code': line[0],
                    'batch_count': line[1:7],
                    'block_count': line[7:13],
                    'entry_addenda_count': line[13:21],
                    'entry_hash': line[21:31],
                    'total_debit_amount': "{:.2f}".format(total_debit_amount),
                    'total_credit_amount': "{:.2f}".format(total_credit_amount),
                    'reserved': line[55:94].strip()
                }

        except Exception as e:
            logger.error(f"Error parsing line {i+1}: {str(e)}")
            flash(f"Error parsing line {i+1}: {str(e)}", 'error')

    if current_batch and 'Batch Control' not in current_batch:
        logger.warning("Final batch missing Batch Control record")
        current_batch['Entries'] = current_batch_entries
        data['Batches'].append(current_batch)
        flash("Warning: Last batch found without a Batch Control Record.", 'warning')

    logger.debug(f"Parsing complete. Found {len(data.get('Batches', []))} batches")
    return dict(data)

# Modified to accept a list of batches, where each batch contains its header and entries
def generate_nacha_file(file_header_data, all_batches_data):
    """Generate NACHA file content from form data and dummy entries."""
    logger.debug("Generating NACHA file content")
    nacha_lines = []

    # File Header Record
    imm_dest = file_header_data['immediate_destination'].rjust(10, ' ')
    imm_origin = file_header_data['immediate_origin'].rjust(10, ' ')
    file_id_modifier = file_header_data['file_id_modifier'].ljust(1)

    file_header_line = (
        f"1"
        f"{file_header_data['priority_code'].ljust(2)}"
        f"{imm_dest}"
        f"{imm_origin}"
        f"{file_header_data['file_creation_date'].ljust(6)}"
        f"{file_header_data['file_creation_time'].ljust(4)}"
        f"{file_header_data['file_id_modifier'].ljust(1)}"
        f"{file_header_data['record_size'].zfill(3)}"
        f"{file_header_data['blocking_factor'].zfill(3)}"
        f"{file_header_data['format_code'].ljust(1)}"
        f"{file_header_data['immediate_destination_name'].ljust(23)}"
        f"{file_header_data['immediate_origin_name'].ljust(23)}"
        f"{file_header_data['reference_code'].ljust(8)}"
    )
    nacha_lines.append(file_header_line)

    total_file_debit_amount = 0
    total_file_credit_amount = 0
    total_file_entry_addenda_count = 0
    total_file_entry_hash = 0
    total_batch_count = 0
    current_line_count = 1

    # Loop through each batch
    for batch_data in all_batches_data:
        total_batch_count += 1
        batch_debit_amount = 0
        batch_credit_amount = 0
        batch_entry_addenda_count = 0
        batch_entry_hash = 0

        batch_header = batch_data['batch_header']
        entries = batch_data['entries']

        settlement_date = batch_header['settlement_date'].ljust(3)
        batch_number = str(batch_header['batch_number']).zfill(7)

        # Batch Header Record
        batch_header_line = (
            f"5"
            f"{batch_header['service_class_code'].ljust(3)}"
            f"{batch_header['company_name'].ljust(16)}"
            f"{batch_header['company_discretionary_data'].ljust(20)}"
            f"{batch_header['company_identification'].ljust(10)}"
            f"{batch_header['standard_entry_class_code'].ljust(3)}"
            f"{batch_header['company_entry_description'].ljust(10)}"
            f"{batch_header['descriptive_date'].ljust(6)}"
            f"{batch_header['effective_entry_date'].ljust(6)}"
            f"{settlement_date}"
            f"{batch_header['originator_status_code'].ljust(1)}"
            f"{batch_header['originating_dfi_identification'].ljust(8)}"
            f"{batch_number}"
        )
        nacha_lines.append(batch_header_line)
        current_line_count += 1

        # Entry Detail Records for the current batch
        for entry in entries:
            transaction_code = entry['transaction_code'].ljust(2)
            receiving_dfi_id = entry['receiving_dfi_identification'].ljust(8)
            check_digit = entry['check_digit'].ljust(1)
            dfi_account_number = entry['dfi_account_number'].ljust(17)

            try:
                amount_cents = int(float(entry['amount']) * 100)
                amount_str = str(amount_cents).zfill(10)
            except ValueError:
                logger.warning(f"Invalid amount '{entry['amount']}' for an entry. Using 0.")
                amount_str = "0000000000"

            individual_id_number = entry['individual_identification_number'].ljust(15)
            individual_name = entry['individual_name'].ljust(22)
            discretionary_data = entry['discretionary_data'].ljust(2)
            addenda_indicator = entry['addenda_record_indicator'].ljust(1)
            trace_number = entry['trace_number'].ljust(15)

            entry_detail_line = (
                f"6"
                f"{transaction_code}"
                f"{receiving_dfi_id}"
                f"{check_digit}"
                f"{dfi_account_number}"
                f"{amount_str}"
                f"{individual_id_number}"
                f"{individual_name}"
                f"{discretionary_data}"
                f"{addenda_indicator}"
                f"{trace_number}"
            )
            nacha_lines.append(entry_detail_line)
            current_line_count += 1
            batch_entry_addenda_count += 1

            # Update batch totals
            transaction_type, _ = determine_transaction_type(entry['transaction_code'])
            if transaction_type == 'Debit':
                batch_debit_amount += float(entry['amount'])
            elif transaction_type == 'Credit':
                batch_credit_amount += float(entry['amount'])

            try:
                batch_entry_hash += int(receiving_dfi_id[:8])
            except ValueError:
                logger.warning(f"Could not add DFI ID '{receiving_dfi_id[:8]}' to batch hash")

        # Batch Control Record for the current batch
        batch_control_line = (
            f"8"
            f"{batch_header['service_class_code'].ljust(3)}"
            f"{str(batch_entry_addenda_count).zfill(6)}"
            f"{str(batch_entry_hash % 10000000000).zfill(10)}"
            f"{str(int(batch_debit_amount * 100)).zfill(12)}"
            f"{str(int(batch_credit_amount * 100)).zfill(12)}"
            f"{batch_header['company_identification'].ljust(10)}"
            f"{' ' * 19}"
            f"{' ' * 6}"
            f"{batch_header['originating_dfi_identification'].ljust(8)}"
            f"{batch_number}"
        )
        nacha_lines.append(batch_control_line)
        current_line_count += 1

        # Accumulate file totals
        total_file_debit_amount += batch_debit_amount
        total_file_credit_amount += batch_credit_amount
        total_file_entry_addenda_count += batch_entry_addenda_count
        total_file_entry_hash += batch_entry_hash

    # File Control Record
    blocking_factor = int(file_header_data.get('blocking_factor', '10'))
    temp_line_count = current_line_count + 1 # +1 for the file control record itself
    padding_needed = 0
    if blocking_factor > 0:
        padding_needed = (blocking_factor - (temp_line_count % blocking_factor)) % blocking_factor

    file_block_count_calc = (temp_line_count + padding_needed) // blocking_factor

    file_control_line = (
        f"9"
        f"{str(total_batch_count).zfill(6)}"
        f"{str(file_block_count_calc).zfill(6)}"
        f"{str(total_file_entry_addenda_count).zfill(8)}"
        f"{str(total_file_entry_hash % 10000000000).zfill(10)}"
        f"{str(int(total_file_debit_amount * 100)).zfill(12)}"
        f"{str(int(total_file_credit_amount * 100)).zfill(12)}"
        f"{' ' * 39}"
    )
    nacha_lines.append(file_control_line)
    current_line_count += 1

    # File Padding Records
    for _ in range(padding_needed):
        nacha_lines.append('9' * NACHA_RECORD_LENGTH)
        current_line_count += 1

    logger.debug(f"Generated {current_line_count} NACHA records")
    return "\n".join(nacha_lines)

@app.route("/", methods=["GET"], endpoint='index')
def home():
    return render_template("index.html")

@app.route("/parse", methods=["POST"])
def handle_parse_request():
    logger.debug("Received parse request")

    if 'nacha_file' not in request.files:
        logger.error("No file part in request")
        flash('No file part in the request.', 'error')
        return redirect(url_for('index'))

    file = request.files['nacha_file']
    logger.debug(f"Processing file: {file.filename}")

    if file.filename == '':
        logger.error("Empty filename")
        flash('No selected file.', 'error')
        return redirect(url_for('index'))

    if not file.filename.lower().endswith('.txt'):
        logger.error("Invalid file extension")
        flash('Please upload a .txt file.', 'error')
        return redirect(url_for('index'))

    try:
        content = file.read().decode("utf-8")
        logger.debug("File read successfully")

        if not content.strip():
            logger.error("Empty file content")
            flash('The uploaded file is empty.', 'warning')
            return redirect(url_for('index'))

        data = parse_nacha_grouped(content)
        logger.debug(f"Parsed data structure: {bool(data)}")

        if not data or not data.get('File Header'):
            logger.error("Invalid NACHA format")
            flash('Invalid or unsupported NACHA file format detected.', 'error')
            return redirect(url_for('index'))

        logger.debug("Rendering parse results template")
        return render_template("parse_results.html", data=data)

    except UnicodeDecodeError:
        logger.error("Unicode decode error")
        flash('Failed to decode file. Please ensure it is a plain text (UTF-8) file.', 'error')
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        flash(f'An unexpected error occurred while processing the file: {str(e)}', 'error')

    return redirect(url_for('index'))

@app.route("/create", methods=["GET"])
def show_create_form():
    now = datetime.datetime.now()
    today_yy_mm_dd = now.strftime('%y%m%d')
    current_time_hh_mm = now.strftime('%H%M')
    tomorrow = now + datetime.timedelta(days=1)
    tomorrow_yy_mm_dd = tomorrow.strftime('%y%m%d')

    return render_template("create_nacha.html",
                           today_date=today_yy_mm_dd,
                           current_time=current_time_hh_mm,
                           tomorrow_date=tomorrow_yy_mm_dd)

@app.route("/generate", methods=["POST"])
def handle_create_request():
    try:
        now_gen = datetime.datetime.now()
        file_date_yy_mm_dd_gen = now_gen.strftime('%y%m%d')
        file_time_hh_mm_gen = now_gen.strftime('%H%M')
        effective_entry_date_gen = (now_gen + datetime.timedelta(days=1)).strftime('%y%m%d')

        # File Header Data (remains the same)
        file_header = {
            'priority_code': request.form.get('priority_code', '01'),
            'immediate_destination': request.form.get('immediate_destination', '').strip(),
            'immediate_origin': request.form.get('immediate_origin', '').strip(),
            'file_creation_date': request.form.get('file_creation_date') or file_date_yy_mm_dd_gen,
            'file_creation_time': request.form.get('file_creation_time') or file_time_hh_mm_gen,
            'file_id_modifier': request.form.get('file_id_modifier', 'A').upper(),
            'record_size': request.form.get('record_size', '094'),
            'blocking_factor': request.form.get('blocking_factor', '10'),
            'format_code': request.form.get('format_code', '1'),
            'immediate_destination_name': request.form.get('immediate_destination_name', '').ljust(23)[:23],
            'immediate_origin_name': request.form.get('immediate_origin_name', '').ljust(23)[:23],
            'reference_code': request.form.get('reference_code', '').ljust(8)[:8]
        }

        # Validate File Header
        if not file_header['immediate_destination'] or not file_header['immediate_destination'].isdigit() or len(file_header['immediate_destination']) != 9:
            flash('File Header: Immediate Destination (Routing #) must be 9 digits.', 'error')
            return redirect(url_for('show_create_form'))
        if not file_header['immediate_origin'] or not file_header['immediate_origin'].isdigit() or len(file_header['immediate_origin']) != 9:
            flash('File Header: Immediate Origin (Company ID) must be 9 digits.', 'error')
            return redirect(url_for('show_create_form'))
        try:
            bf = int(file_header['blocking_factor'])
            if bf <= 0:
                raise ValueError("Blocking factor must be positive.")
        except ValueError:
            flash('File Header: Blocking Factor must be a positive number.', 'error')
            return redirect(url_for('show_create_form'))

        all_batches_data = [] # List to hold data for all batches

        # Get the total number of batches from the hidden input field
        try:
            num_batches = int(request.form.get('num_batches', 0))
        except ValueError:
            flash("Invalid total number of batches submitted.", 'error')
            return redirect(url_for('show_create_form'))

        if num_batches == 0:
            flash("No batches provided. A NACHA file needs at least one batch with entries.", 'error')
            return redirect(url_for('show_create_form'))

        for batch_idx in range(num_batches):
            # Batch Header Data for current batch
            batch_header = {
                'service_class_code': request.form.get(f'batch_{batch_idx}_service_class_code', '200'),
                'company_name': request.form.get(f'batch_{batch_idx}_company_name', '').ljust(16)[:16],
                'company_discretionary_data': request.form.get(f'batch_{batch_idx}_company_discretionary_data', '').ljust(20)[:20],
                'company_identification': request.form.get(f'batch_{batch_idx}_company_identification', '').ljust(10)[:10],
                'standard_entry_class_code': request.form.get(f'batch_{batch_idx}_standard_entry_class_code', 'PPD').ljust(3)[:3],
                'company_entry_description': request.form.get(f'batch_{batch_idx}_company_entry_description', '').ljust(10)[:10],
                'descriptive_date': request.form.get(f'batch_{batch_idx}_descriptive_date', '').ljust(6)[:6],
                'effective_entry_date': request.form.get(f'batch_{batch_idx}_effective_entry_date', effective_entry_date_gen),
                'settlement_date': request.form.get(f'batch_{batch_idx}_settlement_date', '   ').ljust(3)[:3],
                'originator_status_code': request.form.get(f'batch_{batch_idx}_originator_status_code', '1'),
                'originating_dfi_identification': request.form.get(f'batch_{batch_idx}_originating_dfi_identification', '').ljust(8)[:8],
                'batch_number': request.form.get(f'batch_{batch_idx}_batch_number', str(batch_idx + 1)).zfill(7)
            }

            # Validate Batch Header
            if not batch_header['company_identification'] or len(batch_header['company_identification']) != 10:
                flash(f'Batch {batch_idx + 1}: Company Identification must be 10 characters (can include leading zero for 9-digit EIN).', 'error')
                return redirect(url_for('show_create_form'))
            if not batch_header['originating_dfi_identification'] or not batch_header['originating_dfi_identification'].isdigit() or len(batch_header['originating_dfi_identification']) != 8:
                flash(f'Batch {batch_idx + 1}: Originating DFI Identification must be 8 digits.', 'error')
                return redirect(url_for('show_create_form'))

            # Entry Details for current batch
            current_batch_entries = []
            try:
                num_entries_for_batch = int(request.form.get(f'num_entries_batch_{batch_idx}', 0))
            except ValueError:
                flash(f"Batch {batch_idx + 1}: Invalid number of entries submitted.", 'error')
                return redirect(url_for('show_create_form'))

            if num_entries_for_batch == 0:
                flash(f"Batch {batch_idx + 1}: No entries provided. Each batch needs at least one entry.", 'error')
                return redirect(url_for('show_create_form'))

            for entry_idx in range(num_entries_for_batch):
                entry_data = {
                    'transaction_code': request.form.get(f'entry_{batch_idx}_{entry_idx}_transaction_code', '27').strip(),
                    'receiving_dfi_identification': request.form.get(f'entry_{batch_idx}_{entry_idx}_receiving_dfi_identification', '').strip(),
                    'check_digit': request.form.get(f'entry_{batch_idx}_{entry_idx}_check_digit', '').strip(),
                    'dfi_account_number': request.form.get(f'entry_{batch_idx}_{entry_idx}_dfi_account_number', '').strip(),
                    'amount': request.form.get(f'entry_{batch_idx}_{entry_idx}_amount', '0.00').strip(),
                    'individual_identification_number': request.form.get(f'entry_{batch_idx}_{entry_idx}_individual_identification_number', '').strip(),
                    'individual_name': request.form.get(f'entry_{batch_idx}_{entry_idx}_individual_name', '').strip(),
                    'discretionary_data': request.form.get(f'entry_{batch_idx}_{entry_idx}_discretionary_data', '').strip(),
                    'addenda_record_indicator': request.form.get(f'entry_{batch_idx}_{entry_idx}_addenda_record_indicator', '0').strip(),
                    'trace_number': request.form.get(f'entry_{batch_idx}_{entry_idx}_trace_number', '').strip()
                }

                # Server-side validation for each entry
                if not entry_data['transaction_code'] or not entry_data['transaction_code'].isdigit() or len(entry_data['transaction_code']) != 2:
                    flash(f'Batch {batch_idx + 1}, Entry {entry_idx + 1}: Transaction Code must be 2 digits.', 'error')
                    return redirect(url_for('show_create_form'))
                if not entry_data['receiving_dfi_identification'] or not entry_data['receiving_dfi_identification'].isdigit() or len(entry_data['receiving_dfi_identification']) != 8:
                    flash(f'Batch {batch_idx + 1}, Entry {entry_idx + 1}: Receiving DFI Identification must be 8 digits.', 'error')
                    return redirect(url_for('show_create_form'))
                if not entry_data['check_digit'] or not entry_data['check_digit'].isdigit() or len(entry_data['check_digit']) != 1:
                    flash(f'Batch {batch_idx + 1}, Entry {entry_idx + 1}: Check Digit must be a single digit.', 'error')
                    return redirect(url_for('show_create_form'))
                if not entry_data['dfi_account_number'] or len(entry_data['dfi_account_number']) > 17:
                    flash(f'Batch {batch_idx + 1}, Entry {entry_idx + 1}: DFI Account Number is required and max 17 characters.', 'error')
                    return redirect(url_for('show_create_form'))
                try:
                    float(entry_data['amount'])
                except ValueError:
                    flash(f'Batch {batch_idx + 1}, Entry {entry_idx + 1}: Amount must be a valid number (e.g., 123.45).', 'error')
                    return redirect(url_for('show_create_form'))
                if not entry_data['individual_name'] or len(entry_data['individual_name']) > 22:
                    flash(f'Batch {batch_idx + 1}, Entry {entry_idx + 1}: Individual Name is required and max 22 characters.', 'error')
                    return redirect(url_for('show_create_form'))
                if not entry_data['trace_number'] or not entry_data['trace_number'].isdigit() or len(entry_data['trace_number']) != 15:
                    flash(f'Batch {batch_idx + 1}, Entry {entry_idx + 1}: Trace Number must be 15 digits.', 'error')
                    return redirect(url_for('show_create_form'))

                current_batch_entries.append(entry_data)
            
            # Append the current batch's header and its entries to the main list
            all_batches_data.append({
                'batch_header': batch_header,
                'entries': current_batch_entries
            })


        nacha_content = generate_nacha_file(file_header, all_batches_data)

        file_obj = BytesIO()
        file_obj.write(nacha_content.encode('utf-8'))
        file_obj.seek(0)

        flash('NACHA file generated successfully!', 'success')
        return send_file(
            file_obj,
            as_attachment=True,
            download_name=f'NACHA_{now_gen.strftime("%Y%m%d_%H%M%S")}.txt',
            mimetype='text/plain'
        )
    except Exception as e:
        logger.error(f"Error generating NACHA file: {str(e)}", exc_info=True)
        flash(f'An unexpected error occurred while creating the file: {str(e)}', 'error')
        return redirect(url_for('show_create_form'))

if __name__ == "__main__":
    app.run(debug=True)