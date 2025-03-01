from flask import Flask, request, render_template, send_from_directory, redirect, url_for, session, Response
import os
import json
import re
from datetime import datetime, timedelta
from urllib.parse import quote
from functools import wraps
from queue import Queue

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Clave secreta para manejar sesiones
UPLOAD_FOLDER = "uploads"  # Carpeta para almacenar archivos subidos
MESSAGES_FILE = "messages.json"  # Archivo para almacenar mensajes
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Listas para almacenar las colas de los clientes suscritos
message_clients = []
file_clients = []

# Decorador para requerir login
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Decorador para requerir verificación (solo para verificados verdes, no testers)
def verified_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('verified'):
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# Cargar usuarios desde JSON
def load_users():
    if not os.path.exists('users.json'):
        return []
    with open('users.json', 'r') as f:
        return json.load(f)

# Guardar usuarios en JSON
def save_users(users):
    with open('users.json', 'w') as f:
        json.dump(users, f, indent=4)

# Cargar mensajes desde JSON
def load_messages():
    if not os.path.exists(MESSAGES_FILE):
        return []
    with open(MESSAGES_FILE, 'r') as f:
        return json.load(f)

# Guardar mensajes en JSON
def save_messages(messages):
    with open(MESSAGES_FILE, 'w') as f:
        json.dump(messages, f, indent=4)

# Verificar el estado de verificación y bans del usuario
def check_verification_and_bans():
    if 'logged_in' in session:
        users = load_users()
        user = next((u for u in users if u['username'] == session['username']), None)
        if user:
            session['verified'] = user.get('verified', False)  # Verificado verde
            session['tester'] = user.get('tester', False)      # Verificado azul (tester)
            session['bans'] = user.get('bans', {})
            # Verificar si las restricciones han expirado
            bans = session['bans']
            for ban_type in list(bans.keys()):
                ban_info = bans[ban_type]
                start_time = datetime.strptime(ban_info['start_time'], '%Y-%m-%d %H:%M:%S')
                duration = timedelta(minutes=ban_info['duration'])
                if datetime.now() > start_time + duration:
                    del bans[ban_type]
                    user['bans'] = bans
                    save_users(users)

@app.before_request
def before_request():
    check_verification_and_bans()

# Rutas de autenticación
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        users = load_users()
        username_or_email = request.form['username_or_email']
        password = request.form['password']
        
        user = next((u for u in users if (u['username'] == username_or_email or u['email'] == username_or_email) and u['password'] == password), None)
        
        if user:
            session['logged_in'] = True
            session['username'] = user['username']
            session['email'] = user['email']
            session['verified'] = user.get('verified', False)
            session['tester'] = user.get('tester', False)
            session['bans'] = user.get('bans', {})
            if session['bans'].get('full_ban'):
                return redirect(url_for('banned'))
            return redirect(url_for('index'))
        return render_template('login.html', error="Credenciales incorrectas")
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        users = load_users()
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
            return render_template('register.html', error="Correo electrónico no válido")
        
        if any(u['email'] == email for u in users):
            return render_template('register.html', error="El correo electrónico ya está registrado")
        if any(u['username'] == username for u in users):
            return render_template('register.html', error="El nombre de usuario ya existe")
        
        users.append({
            'username': username,
            'email': email,
            'password': password,
            'verified': False,
            'tester': False,  # Por defecto no es tester
            'bans': {}
        })
        save_users(users)
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# Pantalla de ban
@app.route('/banned')
@login_required
def banned():
    ban_info = session['bans'].get('full_ban', {})
    return render_template('banned.html', ban_info=ban_info)

# Rutas principales
@app.route("/")
@login_required
def index():
    if session['bans'].get('full_ban'):
        return redirect(url_for('banned'))
    
    # Calcular tiempos de finalización para las restricciones
    bans_info = session['bans']
    bans_with_end_time = {}
    for ban_type, ban_info in bans_info.items():
        start_time = datetime.strptime(ban_info['start_time'], '%Y-%m-%d %H:%M:%S')
        end_time = start_time + timedelta(minutes=ban_info['duration'])
        bans_with_end_time[ban_type] = {
            'reason': ban_info['reason'],
            'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S')
        }
    
    return render_template(
        "index.html",
        username=session['username'],
        verified=session['verified'],
        tester=session['tester'],
        bans=bans_with_end_time
    )

@app.route("/upload", methods=["POST"])
@login_required
def upload_file():
    if session['bans'].get('full_ban') or session['bans'].get('upload_restriction'):
        return redirect(url_for('index'))
    if "file" not in request.files:
        return "No file part"
    file = request.files["file"]
    if file.filename == "":
        return "No selected file"
    
    file_path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(file_path)
    
    file_info = {
        "name": file.filename,
        "owner": session['username'],
        "owner_verified": session['verified'],
        "owner_tester": session['tester'],
        "upload_date": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "size": f"{os.path.getsize(file_path) / 1024:.2f} KB"
    }
    
    files_info = []
    if os.path.exists('files_info.json'):
        with open('files_info.json', 'r') as f:
            files_info = json.load(f)
    
    files_info.append(file_info)
    with open('files_info.json', 'w') as f:
        json.dump(files_info, f, indent=4)
    
    for client_queue in file_clients[:]:
        try:
            client_queue.put(file_info)
        except:
            file_clients.remove(client_queue)
    
    return redirect(url_for('list_files'))

@app.route("/files")
@login_required
def list_files():
    if os.path.exists('files_info.json'):
        with open('files_info.json', 'r') as f:
            files_info = json.load(f)
    else:
        files_info = []
    return render_template(
        "files.html",
        files=files_info,
        tester=session['tester']
    )

@app.route("/download/<filename>")
@login_required
def download_file(filename):
    filename = filename.encode('utf-8').decode('utf-8')
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

@app.route("/delete/<filename>", methods=["POST"])
@login_required
def delete_file(filename):
    file_info = get_file_info(filename)
    if not file_info:
        return "Archivo no encontrado", 404
    
    if session['username'] == file_info['owner'] or (session['verified'] and not file_info['owner_verified']):
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.exists(file_path):
            os.remove(file_path)
        
        if os.path.exists('files_info.json'):
            with open('files_info.json', 'r') as f:
                files_info = json.load(f)
            files_info = [fi for fi in files_info if fi['name'] != filename]
            with open('files_info.json', 'w') as f:
                json.dump(files_info, f, indent=4)
        
        for client_queue in file_clients[:]:
            try:
                client_queue.put({"action": "delete", "name": filename})
            except:
                file_clients.remove(client_queue)
        
        return redirect(url_for('list_files'))
    else:
        return "No tienes permiso para eliminar este archivo", 403

@app.route("/send_message", methods=["POST"])
@login_required
def send_message():
    if session['bans'].get('full_ban') or session['bans'].get('message_restriction'):
        return redirect(url_for('index'))
    message = request.form.get('message')
    if not message:
        return "Mensaje vacío", 400
    
    messages = load_messages()
    new_message = {
        "username": session['username'],
        "message": message,
        "verified": session['verified'],
        "tester": session['tester'],
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    messages.append(new_message)
    save_messages(messages)

    for client_queue in message_clients[:]:
        try:
            client_queue.put(new_message)
        except:
            message_clients.remove(client_queue)

    return redirect(url_for('show_messages'))

@app.route("/delete_message/<int:message_id>", methods=["POST"])
@login_required
def delete_message(message_id):
    messages = load_messages()
    if message_id < 0 or message_id >= len(messages):
        return "Mensaje no encontrado", 404
    
    message = messages[message_id]
    if session['username'] == message['username'] or (session['verified'] and not message['verified']):
        messages.pop(message_id)
        save_messages(messages)
        
        for client_queue in message_clients[:]:
            try:
                client_queue.put({"action": "delete", "id": message_id})
            except:
                message_clients.remove(client_queue)
        
        return redirect(url_for('show_messages'))
    else:
        return "No tienes permiso para eliminar este mensaje", 403

@app.route("/stream_messages")
@login_required
def stream_messages():
    def event_stream():
        client_queue = Queue()
        message_clients.append(client_queue)
        
        try:
            while True:
                message = client_queue.get()
                yield f"data: {json.dumps(message)}\n\n"
        except GeneratorExit:
            if client_queue in message_clients:
                message_clients.remove(client_queue)

    return Response(event_stream(), content_type="text/event-stream")

@app.route("/stream_files")
@login_required
def stream_files():
    def event_stream():
        client_queue = Queue()
        file_clients.append(client_queue)
        
        try:
            while True:
                file_event = client_queue.get()
                yield f"data: {json.dumps(file_event)}\n\n"
        except GeneratorExit:
            if client_queue in file_clients:
                file_clients.remove(client_queue)
    
    return Response(event_stream(), content_type="text/event-stream")

@app.route("/messages")
@login_required
def show_messages():
    messages = load_messages()
    return render_template(
        "messages.html",
        messages=messages,
        username=session['username'],
        verified=session['verified'],
        tester=session['tester']
    )

# Dashboard para usuarios verificados (solo verdes)
@app.route("/dashboard", methods=['GET', 'POST'])
@login_required
@verified_required
def dashboard():
    users = load_users()
    if request.method == 'POST':
        target_username = request.form['username']
        action = request.form['action']
        duration = int(request.form['duration'])  # En minutos
        reason = request.form['reason']
        
        for user in users:
            if user['username'] == target_username:
                user['bans'] = user.get('bans', {})
                if action == 'ban':
                    user['bans']['full_ban'] = {
                        'reason': reason,
                        'duration': duration,
                        'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                elif action == 'restrict_messages':
                    user['bans']['message_restriction'] = {
                        'reason': reason,
                        'duration': duration,
                        'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                elif action == 'restrict_uploads':
                    user['bans']['upload_restriction'] = {
                        'reason': reason,
                        'duration': duration,
                        'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                save_users(users)
                break
    return render_template('dashboard.html', users=users)

def get_file_info(filename):
    if os.path.exists('files_info.json'):
        with open('files_info.json', 'r') as f:
            files_info = json.load(f)
            for file_info in files_info:
                if file_info['name'] == filename:
                    return file_info
    return None

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
