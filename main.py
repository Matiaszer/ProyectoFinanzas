import sqlite3
from pathlib import Path
import base64
import io

import requests
from flask import Flask, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = 'finanzas-secret-key-2026'

api_key = 'c4a9a9b322da4cc0b55383b6d4d98b30'
direccion_api = f'https://api.currencyfreaks.com/latest?apikey={api_key}'

DB_PATH = Path(__file__).with_name('finanzas.db')
base_currency = 'CLP'
rates_cache = {}

currency_aliases = {
    'PESO': 'CLP'
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            moneda TEXT NOT NULL DEFAULT 'CLP',
            objetivo REAL DEFAULT 0,
            objetivo_nombre TEXT DEFAULT '',
            objetivo_imagen TEXT DEFAULT ''
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            monto REAL NOT NULL,
            moneda TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        '''
    )
    
    # Agregar columnas si no existen (migración)
    cursor = conn.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cursor.fetchall()]
    if 'objetivo' not in columns:
        conn.execute('ALTER TABLE users ADD COLUMN objetivo REAL DEFAULT 0')
    if 'objetivo_nombre' not in columns:
        conn.execute('ALTER TABLE users ADD COLUMN objetivo_nombre TEXT DEFAULT ""')
    if 'objetivo_imagen' not in columns:
        conn.execute('ALTER TABLE users ADD COLUMN objetivo_imagen TEXT DEFAULT ""')
    
    conn.commit()
    conn.close()


def normalize_currency(moneda):
    if not moneda:
        return base_currency
    return currency_aliases.get(moneda.strip().upper(), moneda.strip().upper())


def obtener_tasas():
    global rates_cache
    try:
        respuesta = requests.get(direccion_api, timeout=6)
        respuesta.raise_for_status()
        datos = respuesta.json()
        rates = datos.get('rates', {})
        if rates:
            rates_cache = {k: float(v) for k, v in rates.items()}
        return rates_cache
    except Exception as e:
        print('Error al conectar con la API de monedas:', e)
        return rates_cache


def tasa_moneda(moneda):
    moneda = normalize_currency(moneda)
    tasas = obtener_tasas()
    if not tasas:
        raise ValueError('No hay tasas disponibles')
    if moneda not in tasas:
        raise ValueError(f'Tasa no encontrada para {moneda}')
    return tasas[moneda]


def convert_to_base(monto, moneda):
    moneda = normalize_currency(moneda)
    monto = float(monto)
    if moneda == base_currency:
        return monto
    tasa_mon = tasa_moneda(moneda)
    tasa_base = tasa_moneda(base_currency)
    # Convertir primero a USD y luego a CLP.
    usd = monto / tasa_mon
    return usd * tasa_base


def convert_from_base(monto, moneda):
    moneda = normalize_currency(moneda)
    monto = float(monto)
    if moneda == base_currency:
        return monto
    tasa_base = tasa_moneda(base_currency)
    tasa_target = tasa_moneda(moneda)
    usd = monto / tasa_base
    return usd * tasa_target


def get_current_user_id():
    return session.get('user_id')


def get_current_user():
    user_id = get_current_user_id()
    if not user_id:
        return None
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return user


def get_user_currency(user_id):
    conn = get_db()
    row = conn.execute('SELECT moneda FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return row['moneda'] if row else base_currency


def get_balance_for_user(user_id):
    conn = get_db()
    movimientos = conn.execute(
        'SELECT id, tipo, descripcion, monto, moneda FROM movimientos WHERE user_id = ? ORDER BY id DESC',
        (user_id,),
    ).fetchall()
    usuario = conn.execute(
        'SELECT objetivo, objetivo_nombre, objetivo_imagen FROM users WHERE id = ?',
        (user_id,),
    ).fetchone()
    conn.close()

    gastos = {}
    ingresos = {}
    total_gastos = 0.0
    total_ingresos = 0.0
    movimientos_formateados = []
    moneda_actual = get_user_currency(user_id)
    objetivo = usuario['objetivo'] if usuario else 0
    objetivo_nombre = usuario['objetivo_nombre'] if usuario else ''
    objetivo_imagen = usuario['objetivo_imagen'] if usuario else ''

    for movimiento in movimientos:
        tipo = movimiento['tipo']
        descripcion = movimiento['descripcion']
        monto = float(movimiento['monto'])
        if tipo == 'gasto':
            gastos[descripcion] = gastos.get(descripcion, 0.0) + monto
        elif tipo == 'ingreso':
            ingresos[descripcion] = ingresos.get(descripcion, 0.0) + monto

        movimientos_formateados.append({
            'id': movimiento['id'],
            'tipo': tipo,
            'descripcion': descripcion,
            'monto': round(convert_from_base(monto, moneda_actual), 2),
        })

    total_gastos = sum(gastos.values())
    total_ingresos = sum(ingresos.values())
    saldo_base = total_ingresos - total_gastos
    saldo_convertido = round(convert_from_base(saldo_base, moneda_actual), 2)

    # Calcular porcentaje del objetivo
    porcentaje_objetivo = 0
    if objetivo > 0:
        porcentaje_objetivo = min(round((saldo_convertido / objetivo) * 100, 1), 100)

    return {
        'gastos': {k: round(convert_from_base(v, moneda_actual), 2) for k, v in gastos.items()},
        'ingresos': {k: round(convert_from_base(v, moneda_actual), 2) for k, v in ingresos.items()},
        'total_gastos': round(convert_from_base(total_gastos, moneda_actual), 2),
        'total_ingresos': round(convert_from_base(total_ingresos, moneda_actual), 2),
        'saldo': saldo_convertido,
        'moneda': moneda_actual,
        'objetivo': {
            'monto': objetivo,
            'nombre': objetivo_nombre,
            'imagen': objetivo_imagen,
            'porcentaje': porcentaje_objetivo,
            'falta': max(round(objetivo - saldo_convertido, 2), 0)
        },
        'movimientos': movimientos_formateados,
    }


def save_movement(user_id, tipo, descripcion, monto, moneda):
    conn = get_db()
    conn.execute(
        'INSERT INTO movimientos (user_id, tipo, descripcion, monto, moneda) VALUES (?, ?, ?, ?, ?)',
        (user_id, tipo, descripcion, monto, moneda),
    )
    conn.commit()
    conn.close()


@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.route('/api/register', methods=['POST'])
def register():
    datos = request.get_json(force=True)
    username = str(datos.get('username', '')).strip()
    password = str(datos.get('password', '')).strip()

    if not username or not password:
        return jsonify({'error': 'Usuario y contraseña son obligatorios.'}), 400
    if len(password) < 4:
        return jsonify({'error': 'La contraseña debe tener al menos 4 caracteres.'}), 400

    conn = get_db()
    exists = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
    if exists:
        conn.close()
        return jsonify({'error': 'Ese usuario ya existe.'}), 409

    password_hash = generate_password_hash(password)
    cursor = conn.execute(
        'INSERT INTO users (username, password_hash, moneda) VALUES (?, ?, ?)',
        (username, password_hash, base_currency),
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()

    session['user_id'] = user_id
    return jsonify({'message': 'Registro exitoso.', 'username': username}), 201


@app.route('/api/login', methods=['POST'])
def login():
    datos = request.get_json(force=True)
    username = str(datos.get('username', '')).strip()
    password = str(datos.get('password', '')).strip()

    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()

    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'Usuario o contraseña incorrectos.'}), 401

    session['user_id'] = user['id']
    return jsonify({'message': 'Sesión iniciada.', 'username': username}), 200


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'message': 'Sesión cerrada.'})


@app.route('/api/me', methods=['GET'])
def me():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'No autenticado.'}), 401
    return jsonify({'username': user['username'], 'moneda': user['moneda']})


@app.route('/api/gasto', methods=['POST'])
def agregar_gasto():
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Debes iniciar sesión.'}), 401

    datos = request.get_json(force=True)
    monto = str(datos.get('monto', '')).strip()
    descripcion = str(datos.get('descripcion', '')).strip()
    if not monto or not descripcion:
        return jsonify({'error': 'Monto y descripción son obligatorios.'}), 400
    try:
        float(monto)
    except ValueError:
        return jsonify({'error': 'Monto inválido.'}), 400

    moneda = get_user_currency(user_id)
    try:
        monto_base = convert_to_base(monto, moneda)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    save_movement(user_id, 'gasto', descripcion, monto_base, base_currency)
    saldo = get_balance_for_user(user_id)
    return jsonify({'message': 'Gasto agregado.', 'saldo': saldo['saldo'], 'moneda': saldo['moneda']}), 201


@app.route('/api/ingreso', methods=['POST'])
def agregar_ingreso():
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Debes iniciar sesión.'}), 401

    datos = request.get_json(force=True)
    monto = str(datos.get('monto', '')).strip()
    descripcion = str(datos.get('descripcion', '')).strip()
    if not monto or not descripcion:
        return jsonify({'error': 'Monto y descripción son obligatorios.'}), 400
    try:
        float(monto)
    except ValueError:
        return jsonify({'error': 'Monto inválido.'}), 400

    moneda = get_user_currency(user_id)
    try:
        monto_base = convert_to_base(monto, moneda)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    save_movement(user_id, 'ingreso', descripcion, monto_base, base_currency)
    saldo = get_balance_for_user(user_id)
    return jsonify({'message': 'Ingreso agregado.', 'saldo': saldo['saldo'], 'moneda': saldo['moneda']}), 201


@app.route('/api/movimiento/<int:movimiento_id>', methods=['DELETE'])
def eliminar_movimiento(movimiento_id):
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Debes iniciar sesión.'}), 401

    conn = get_db()
    movimiento = conn.execute(
        'SELECT id FROM movimientos WHERE id = ? AND user_id = ?',
        (movimiento_id, user_id),
    ).fetchone()
    if not movimiento:
        conn.close()
        return jsonify({'error': 'Movimiento no encontrado.'}), 404

    conn.execute('DELETE FROM movimientos WHERE id = ? AND user_id = ?', (movimiento_id, user_id))
    conn.commit()
    conn.close()

    saldo = get_balance_for_user(user_id)
    return jsonify({'message': 'Movimiento eliminado.', 'saldo': saldo['saldo'], 'moneda': saldo['moneda']}), 200



@app.route('/api/saldo', methods=['GET'])
def ver_saldo():
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Debes iniciar sesión.'}), 401

    return jsonify(get_balance_for_user(user_id))


@app.route('/api/config/moneda', methods=['POST'])
def configurar_moneda():
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Debes iniciar sesión.'}), 401

    datos = request.get_json(force=True)
    moneda = normalize_currency(str(datos.get('moneda', '')).strip())
    if moneda not in ['USD', 'EUR', 'GBP', 'JPY', 'CLP']:
        return jsonify({'error': 'Moneda no válida.'}), 400

    try:
        obtener_tasas()
        tasa_moneda(moneda)
    except Exception as e:
        return jsonify({'error': f'No se pudo obtener la tasa de cambio: {str(e)}'}), 503

    conn = get_db()
    conn.execute('UPDATE users SET moneda = ? WHERE id = ?', (moneda, user_id))
    conn.commit()
    conn.close()

    saldo = get_balance_for_user(user_id)
    return jsonify({'message': f'Moneda configurada a {moneda}', 'moneda': moneda, 'saldo': saldo['saldo']})


@app.route('/api/config/objetivo', methods=['POST'])
def configurar_objetivo():
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Debes iniciar sesión.'}), 401

    objetivo_str = request.form.get('objetivo', '').strip()
    objetivo_nombre = request.form.get('nombre', '').strip()
    
    if not objetivo_str:
        return jsonify({'error': 'El objetivo (monto) es obligatorio.'}), 400
    
    try:
        objetivo = float(objetivo_str)
    except ValueError:
        return jsonify({'error': 'Objetivo inválido.'}), 400

    objetivo_imagen = ''
    
    # Procesar imagen si se carga
    if 'imagen' in request.files:
        file = request.files['imagen']
        if file and file.filename:
            # Leer archivo y convertir a base64
            try:
                contenido = file.read()
                objetivo_imagen = base64.b64encode(contenido).decode('utf-8')
            except Exception as e:
                return jsonify({'error': f'Error al procesar imagen: {str(e)}'}), 400

    conn = get_db()
    conn.execute(
        'UPDATE users SET objetivo = ?, objetivo_nombre = ?, objetivo_imagen = ? WHERE id = ?',
        (objetivo, objetivo_nombre, objetivo_imagen, user_id)
    )
    conn.commit()
    conn.close()

    saldo = get_balance_for_user(user_id)
    return jsonify({
        'message': f'Objetivo establecido en {objetivo}',
        'objetivo': saldo['objetivo'],
        'saldo': saldo['saldo']
    })


@app.route('/api/config/objetivo/reset', methods=['POST'])
def resetear_objetivo():
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Debes iniciar sesión.'}), 401
    conn = get_db()
    conn.execute(
    '''
    UPDATE users
    SET objetivo = 0,
        objetivo_nombre = '',
        objetivo_imagen = ''
    WHERE id = ?
    ''',
    (user_id,)
    )
    conn.commit()
    conn.close()
    saldo = get_balance_for_user(user_id)
    return jsonify({
        'message': 'Objetivo eliminado.',
        'objetivo': saldo['objetivo'],
        'saldo': saldo['saldo']
    })


init_db()
obtener_tasas()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
