"""
Сервер лицензий для TwitchFarm
API для проверки и управления подписками
С поддержкой PostgreSQL для постоянного хранения
"""
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import json
import os
from datetime import datetime, timedelta
import hashlib
import secrets
import psycopg2
from psycopg2.extras import RealDictCursor
from urllib.parse import urlparse

app = Flask(__name__)
CORS(app)

DB_FILE = os.getenv("DB_FILE", "licenses.json")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin_twitch_2025")
DATABASE_URL = os.getenv("DATABASE_URL")
USE_POSTGRES = DATABASE_URL is not None and len(DATABASE_URL) > 10


def get_db_connection():
    if not USE_POSTGRES:
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10, sslmode='require')
        return conn
    except Exception as e:
        print(f"[ERROR] Не удалось подключиться к PostgreSQL: {e}")
        return None


def init_postgres_tables():
    if not USE_POSTGRES:
        print("[INFO] PostgreSQL не используется")
        return

    conn = get_db_connection()
    if not conn:
        print("[ERROR] Не удалось подключиться к PostgreSQL!")
        return

    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS licenses (
                license_key VARCHAR(50) PRIMARY KEY,
                client_name VARCHAR(255),
                expires_at VARCHAR(20),
                created_at TIMESTAMP,
                days INTEGER,
                machine_id VARCHAR(255),
                last_check TIMESTAMP,
                first_used TIMESTAMP,
                extended_at TIMESTAMP
            )
        """)
        conn.commit()
        print("✅ PostgreSQL таблицы созданы успешно!")
        cur.execute("SELECT COUNT(*) FROM licenses")
        count = cur.fetchone()[0]
        print(f"📊 Лицензий в базе: {count}")
    except Exception as e:
        print(f"[ERROR] Ошибка создания таблиц: {e}")
    finally:
        if conn:
            conn.close()


def load_db():
    if USE_POSTGRES:
        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("SELECT * FROM licenses")
                rows = cur.fetchall()
                licenses = {}
                for row in rows:
                    licenses[row['license_key']] = {
                        'client_name': row.get('client_name'),
                        'expires_at': row.get('expires_at'),
                        'created_at': row['created_at'].isoformat() if row.get('created_at') else None,
                        'days': row.get('days'),
                        'machine_id': row.get('machine_id'),
                        'last_check': row['last_check'].isoformat() if row.get('last_check') else None,
                        'first_used': row['first_used'].isoformat() if row.get('first_used') else None,
                        'extended_at': row['extended_at'].isoformat() if row.get('extended_at') else None
                    }
                conn.close()
                return {'licenses': licenses}
            except Exception as e:
                print(f"[ERROR] Ошибка загрузки из PostgreSQL: {e}")
                if conn:
                    conn.close()

    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {'licenses': {}}

    return {'licenses': {}}


def save_db(db):
    licenses = db.get('licenses', {})

    if USE_POSTGRES:
        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                for key, data in licenses.items():
                    cur.execute("SELECT license_key FROM licenses WHERE license_key = %s", (key,))
                    exists = cur.fetchone()

                    if exists:
                        cur.execute("""
                            UPDATE licenses SET
                                client_name = %s, expires_at = %s, days = %s,
                                machine_id = %s, last_check = %s, first_used = %s, extended_at = %s
                            WHERE license_key = %s
                        """, (
                            data.get('client_name'), data.get('expires_at'), data.get('days'),
                            data.get('machine_id'),
                            datetime.fromisoformat(data['last_check']) if data.get('last_check') else None,
                            datetime.fromisoformat(data['first_used']) if data.get('first_used') else None,
                            datetime.fromisoformat(data['extended_at']) if data.get('extended_at') else None,
                            key
                        ))
                    else:
                        cur.execute("""
                            INSERT INTO licenses (
                                license_key, client_name, expires_at, created_at, days,
                                machine_id, last_check, first_used, extended_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            key, data.get('client_name'), data.get('expires_at'),
                            datetime.fromisoformat(data['created_at']) if data.get('created_at') else datetime.now(),
                            data.get('days'), data.get('machine_id'),
                            datetime.fromisoformat(data['last_check']) if data.get('last_check') else None,
                            datetime.fromisoformat(data['first_used']) if data.get('first_used') else None,
                            datetime.fromisoformat(data['extended_at']) if data.get('extended_at') else None
                        ))

                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[ERROR] Ошибка сохранения в PostgreSQL: {e}")
                conn.close()

    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] Не удалось сохранить JSON: {e}")


def generate_license_key() -> str:
    parts = [secrets.token_hex(2).upper() for _ in range(4)]
    return '-'.join(parts)


@app.route('/')
def index():
    return render_template_string(ADMIN_PANEL_HTML)


@app.route('/api/check_license', methods=['POST'])
def check_license():
    data = request.json
    license_key = data.get('license_key')
    machine_id = data.get('machine_id')

    if not license_key or not machine_id:
        return jsonify({'valid': False, 'message': 'Missing license_key or machine_id'}), 400

    db = load_db()
    licenses = db.get('licenses', {})

    if license_key not in licenses:
        return jsonify({'valid': False, 'message': 'Неверный лицензионный ключ'})

    license_info = licenses[license_key]

    if license_info.get('machine_id'):
        if license_info['machine_id'] != machine_id:
            return jsonify({'valid': False, 'message': 'Ключ привязан к другому компьютеру'})
    else:
        license_info['machine_id'] = machine_id
        license_info['first_used'] = datetime.now().isoformat()
        licenses[license_key] = license_info
        save_db(db)

    expires_at = license_info.get('expires_at')
    if expires_at:
        expires_date = datetime.fromisoformat(expires_at)
        if datetime.now() > expires_date:
            return jsonify({'valid': False, 'message': 'Подписка закончилась. Свяжитесь с продавцом для продления.'})

        license_info['last_check'] = datetime.now().isoformat()
        licenses[license_key] = license_info
        save_db(db)

        return jsonify({'valid': True, 'expires_at': expires_at, 'message': f'Активна до {expires_at}'})
    else:
        return jsonify({'valid': False, 'message': 'Лицензия не настроена'})


@app.route('/api/version', methods=['GET'])
def check_version():
    return jsonify({
        'min_version': os.getenv('MIN_VERSION', '1.0.0'),
        'telegram': os.getenv('TELEGRAM_CHANNEL', 't.me/ttwfarm')
    })


@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.json
    if data.get('password') == ADMIN_PASSWORD:
        return jsonify({'success': True, 'token': 'admin_token_123'})
    return jsonify({'success': False, 'message': 'Неверный пароль'}), 401


@app.route('/api/admin/licenses', methods=['GET'])
def get_licenses():
    try:
        db = load_db()
        licenses = db.get('licenses', {})
        result = []
        for key, info in licenses.items():
            machine_id = info.get('machine_id', 'Не активирован')
            if machine_id and machine_id != 'Не активирован' and len(machine_id) > 8:
                machine_id = machine_id[:8] + '...'
            result.append({
                'key': key,
                'client_name': info.get('client_name', 'Без имени'),
                'expires_at': info.get('expires_at'),
                'machine_id': machine_id,
                'last_check': info.get('last_check', 'Никогда'),
                'created_at': info.get('created_at'),
            })
        return jsonify(result)
    except Exception as e:
        print(f"[ERROR] get_licenses: {e}")
        return jsonify([]), 500


@app.route('/api/admin/create_license', methods=['POST'])
def create_license():
    data = request.json
    client_name = data.get('client_name', 'Клиент')
    days = int(data.get('days', 30))

    db = load_db()
    licenses = db.get('licenses', {})
    license_key = generate_license_key()
    expires_at = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')

    licenses[license_key] = {
        'client_name': client_name,
        'expires_at': expires_at,
        'created_at': datetime.now().isoformat(),
        'days': days,
        'machine_id': None,
        'last_check': None
    }

    db['licenses'] = licenses
    save_db(db)

    return jsonify({'success': True, 'license_key': license_key, 'expires_at': expires_at})


@app.route('/api/admin/extend_license', methods=['POST'])
def extend_license():
    data = request.json
    license_key = data.get('license_key')
    days = int(data.get('days', 30))

    db = load_db()
    licenses = db.get('licenses', {})

    if license_key not in licenses:
        return jsonify({'success': False, 'message': 'Ключ не найден'}), 404

    current_expires = licenses[license_key].get('expires_at')
    if current_expires:
        current_date = datetime.fromisoformat(current_expires)
        new_expires = (current_date + timedelta(days=days)).strftime('%Y-%m-%d')
    else:
        new_expires = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')

    licenses[license_key]['expires_at'] = new_expires
    licenses[license_key]['extended_at'] = datetime.now().isoformat()
    db['licenses'] = licenses
    save_db(db)

    return jsonify({'success': True, 'new_expires_at': new_expires})


@app.route('/api/admin/delete_license', methods=['POST'])
def delete_license():
    data = request.json
    license_key = data.get('license_key')

    if USE_POSTGRES:
        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute("DELETE FROM licenses WHERE license_key = %s", (license_key,))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[ERROR] Ошибка удаления: {e}")
                conn.close()

    db = load_db()
    licenses = db.get('licenses', {})

    if license_key in licenses:
        del licenses[license_key]
        db['licenses'] = licenses
        save_db(db)
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'message': 'Ключ не найден'}), 404


ADMIN_PANEL_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>TwitchFarm - License Admin</title>
    <meta charset="utf-8">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #1a1a2e; color: #eee; }
        .container { max-width: 1200px; margin: 0 auto; background: #16213e; padding: 20px; border-radius: 10px; }
        h1 { color: #9b59b6; }
        h2 { color: #ccc; }
        button { padding: 10px 20px; margin: 5px; cursor: pointer; border: none; border-radius: 5px; }
        .btn-primary { background: #9b59b6; color: white; }
        .btn-danger { background: #e74c3c; color: white; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #333; }
        th { background: #9b59b6; color: white; }
        .expired { color: #e74c3c; }
        .active { color: #2ecc71; }
        input { padding: 8px; margin: 5px; border: 1px solid #444; border-radius: 4px; background: #0f3460; color: #eee; }
        #newKey { background: #1e3a1e; padding: 15px; border-radius: 8px; margin-top: 20px; display: none; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎮 TwitchFarm - Управление лицензиями</h1>

        <div>
            <h2>Создать новую лицензию</h2>
            <input type="text" id="clientName" placeholder="Имя клиента">
            <input type="number" id="days" placeholder="Дней" value="30">
            <br>
            <button class="btn-primary" onclick="createLicense()" style="margin-top: 15px;">Создать ключ</button>
        </div>

        <div id="newKey">
            <h3>✅ Новый ключ создан:</h3>
            <p><strong id="keyValue" style="font-size: 18px; color: #2ecc71;"></strong></p>
            <p>Действителен до: <span id="expiresValue"></span></p>
        </div>

        <h2>Активные лицензии</h2>
        <button class="btn-primary" onclick="loadLicenses()">Обновить</button>

        <table id="licensesTable">
            <thead>
                <tr>
                    <th>Ключ</th>
                    <th>Клиент</th>
                    <th>Создан</th>
                    <th>Действителен до</th>
                    <th>Последняя активность</th>
                    <th>Действия</th>
                </tr>
            </thead>
            <tbody id="licensesBody">
                <tr><td colspan="6">Загрузка...</td></tr>
            </tbody>
        </table>
    </div>

    <script>
        function createLicense() {
            const clientName = document.getElementById('clientName').value;
            const days = document.getElementById('days').value;

            fetch('/api/admin/create_license', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({client_name: clientName, days: days})
            })
            .then(r => r.json())
            .then(data => {
                document.getElementById('keyValue').textContent = data.license_key;
                document.getElementById('expiresValue').textContent = data.expires_at;
                document.getElementById('newKey').style.display = 'block';
                loadLicenses();
            });
        }

        function loadLicenses() {
            fetch('/api/admin/licenses')
            .then(r => r.json())
            .then(data => {
                const tbody = document.getElementById('licensesBody');
                tbody.innerHTML = '';

                data.forEach(lic => {
                    const tr = document.createElement('tr');
                    const isExpired = new Date(lic.expires_at) < new Date();

                    tr.innerHTML = `
                        <td><code>${lic.key}</code></td>
                        <td>${lic.client_name}</td>
                        <td>${lic.created_at ? lic.created_at.split('T')[0] : '-'}</td>
                        <td class="${isExpired ? 'expired' : 'active'}">${lic.expires_at}</td>
                        <td>${lic.last_check && lic.last_check !== 'Никогда' ? lic.last_check.split('T')[0] : 'Не активирован'}</td>
                        <td>
                            <button class="btn-primary" onclick="extendLicense('${lic.key}')">+30 дней</button>
                            <button class="btn-danger" onclick="deleteLicense('${lic.key}')">Удалить</button>
                        </td>
                    `;
                    tbody.appendChild(tr);
                });
            });
        }

        function extendLicense(key) {
            fetch('/api/admin/extend_license', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({license_key: key, days: 30})
            })
            .then(r => r.json())
            .then(data => {
                alert('✅ Лицензия продлена до: ' + data.new_expires_at);
                loadLicenses();
            });
        }

        function deleteLicense(key) {
            if (confirm('Удалить лицензию ' + key + '?')) {
                fetch('/api/admin/delete_license', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({license_key: key})
                })
                .then(r => r.json())
                .then(() => { loadLicenses(); });
            }
        }

        loadLicenses();
        setInterval(loadLicenses, 30000);
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    print("="*60)
    print("🎮 TwitchFarm - License Server")
    print("="*60)

    port = int(os.getenv("PORT", 5000))
    is_production = os.getenv("RENDER", False)

    if USE_POSTGRES:
        print("💾 Хранилище: PostgreSQL")
        init_postgres_tables()
    else:
        print("⚠️  Хранилище: JSON (временное)")

    if not USE_POSTGRES and not os.path.exists(DB_FILE):
        save_db({'licenses': {}})

    app.run(host='0.0.0.0', port=port, debug=False)
