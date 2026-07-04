import requests
from flask import Flask, jsonify, request

app = Flask(__name__, static_folder='.', static_url_path='')

api_key = 'c4a9a9b322da4cc0b55383b6d4d98b30'
direccion_api = f'https://api.currencyfreaks.com/latest?apikey={api_key}'

# Internally almacenamos valores en CLP como moneda base.
base_currency = 'CLP'
moneda_actual = 'CLP'
rates_cache = {}

gastos = {}
ingresos = {}

currency_aliases = {
    'PESO': 'CLP'
}


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


def calcular_saldo_base():
    return sum(ingresos.values()) * 1.0 - sum(gastos.values()) * 1.0


@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.route('/api/gasto', methods=['POST'])
def agregar_gasto():
    datos = request.get_json(force=True)
    monto = str(datos.get('monto', '')).strip()
    descripcion = str(datos.get('descripcion', '')).strip()
    if not monto or not descripcion:
        return jsonify({'error': 'Monto y descripción son obligatorios.'}), 400
    try:
        float(monto)
    except ValueError:
        return jsonify({'error': 'Monto inválido.'}), 400
    try:
        monto_base = convert_to_base(monto, moneda_actual)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    gastos[descripcion] = monto_base
    saldo = convert_from_base(calcular_saldo_base(), moneda_actual)
    return jsonify({'message': 'Gasto agregado.', 'saldo': saldo}), 201


@app.route('/api/ingreso', methods=['POST'])
def agregar_ingreso():
    datos = request.get_json(force=True)
    monto = str(datos.get('monto', '')).strip()
    descripcion = str(datos.get('descripcion', '')).strip()
    if not monto or not descripcion:
        return jsonify({'error': 'Monto y descripción son obligatorios.'}), 400
    try:
        float(monto)
    except ValueError:
        return jsonify({'error': 'Monto inválido.'}), 400
    try:
        monto_base = convert_to_base(monto, moneda_actual)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    ingresos[descripcion] = monto_base
    saldo = convert_from_base(calcular_saldo_base(), moneda_actual)
    return jsonify({'message': 'Ingreso agregado.', 'saldo': saldo}), 201


@app.route('/api/saldo', methods=['GET'])
def ver_saldo():
    try:
        total_gastos = sum(gastos.values())
        total_ingresos = sum(ingresos.values())
        saldo_base = calcular_saldo_base()
        return jsonify({
            'gastos': {k: round(convert_from_base(v, moneda_actual), 2) for k, v in gastos.items()},
            'ingresos': {k: round(convert_from_base(v, moneda_actual), 2) for k, v in ingresos.items()},
            'total_gastos': round(convert_from_base(total_gastos, moneda_actual), 2),
            'total_ingresos': round(convert_from_base(total_ingresos, moneda_actual), 2),
            'saldo': round(convert_from_base(saldo_base, moneda_actual), 2),
            'moneda': moneda_actual,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/config/moneda', methods=['POST'])
def configurar_moneda():
    global moneda_actual
    datos = request.get_json(force=True)
    moneda = normalize_currency(str(datos.get('moneda', '')).strip())
    if moneda not in ['USD', 'EUR', 'GBP', 'JPY', 'CLP']:
        return jsonify({'error': 'Moneda no válida.'}), 400
    if moneda != base_currency:
        try:
            obtener_tasas()
            tasa_moneda(moneda)
        except Exception as e:
            return jsonify({'error': f'No se pudo obtener la tasa de cambio: {str(e)}'}), 503
    moneda_actual = moneda
    return jsonify({'message': f'Moneda configurada a {moneda_actual}', 'moneda': moneda_actual})


if __name__ == '__main__':
    obtener_tasas()
    app.run(debug=True, port=5000)
