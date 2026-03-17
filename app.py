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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS analytics_events (
                id SERIAL PRIMARY KEY,
                license_key VARCHAR(50),
                app_version VARCHAR(20),
                event_type VARCHAR(30),
                timestamp TIMESTAMP,
                session_duration_minutes INT DEFAULT 0,
                messages_count INT DEFAULT 0,
                donations_count INT DEFAULT 0,
                tts_messages_count INT DEFAULT 0,
                platforms_used TEXT,
                features_used TEXT,
                peak_viewers INT DEFAULT 0,
                avg_viewers INT DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS analytics_sessions (
                license_key VARCHAR(50) PRIMARY KEY,
                first_seen DATE,
                last_seen DATE,
                total_sessions INT DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS analytics_daily (
                date DATE PRIMARY KEY,
                active_users INT DEFAULT 0,
                new_users INT DEFAULT 0,
                total_messages INT DEFAULT 0,
                total_donations INT DEFAULT 0,
                total_tts INT DEFAULT 0,
                avg_session_minutes FLOAT DEFAULT 0,
                total_peak_viewers INT DEFAULT 0,
                avg_viewers_across_clients FLOAT DEFAULT 0
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

@app.route('/analytics/event', methods=['POST'])
def receive_analytics():
    data = request.json
    if not data or not data.get('license_key'):
        return jsonify({'ok': False}), 400
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({'ok': False}), 500
        cur = conn.cursor()
        today = datetime.now().date()
        # Сохраняем событие
        cur.execute("""
            INSERT INTO analytics_events (
                license_key, app_version, event_type, timestamp,
                session_duration_minutes, messages_count, donations_count,
                tts_messages_count, platforms_used, features_used,
                peak_viewers, avg_viewers
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            data.get('license_key'), data.get('app_version'), data.get('event_type'),
            datetime.fromisoformat(data['timestamp']) if data.get('timestamp') else datetime.now(),
            data.get('session_duration_minutes', 0), data.get('messages_count', 0),
            data.get('donations_count', 0), data.get('tts_messages_count', 0),
            json.dumps(data.get('platforms_used', [])), json.dumps(data.get('features_used', [])),
            data.get('peak_viewers', 0), data.get('avg_viewers', 0)
        ))
        # Обновляем сессии
        cur.execute("""
            INSERT INTO analytics_sessions (license_key, first_seen, last_seen, total_sessions)
            VALUES (%s, %s, %s, 1)
            ON CONFLICT (license_key) DO UPDATE SET
                last_seen = EXCLUDED.last_seen,
                total_sessions = analytics_sessions.total_sessions + 1
        """, (data.get('license_key'), today, today))
        # Обновляем дневную статистику
        cur.execute("""
            INSERT INTO analytics_daily (date, active_users, total_messages, total_donations, total_tts, total_peak_viewers)
            VALUES (%s, 1, %s, %s, %s, %s)
            ON CONFLICT (date) DO UPDATE SET
                active_users = analytics_daily.active_users + 1,
                total_messages = analytics_daily.total_messages + EXCLUDED.total_messages,
                total_donations = analytics_daily.total_donations + EXCLUDED.total_donations,
                total_tts = analytics_daily.total_tts + EXCLUDED.total_tts,
                total_peak_viewers = analytics_daily.total_peak_viewers + EXCLUDED.total_peak_viewers
        """, (
            today, data.get('messages_count', 0), data.get('donations_count', 0),
            data.get('tts_messages_count', 0), data.get('peak_viewers', 0)
        ))
        conn.commit()
        conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        print(f"[ANALYTICS] Ошибка: {e}")
        return jsonify({'ok': False}), 500

@app.route('/analytics/dashboard')
def analytics_dashboard():
    password = request.args.get('password') or request.headers.get('X-Password', '')
    if password != ADMIN_PASSWORD:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        conn = get_db_connection()
        if not conn:
            return "DB unavailable", 500
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # DAU — активных сегодня
        cur.execute("SELECT COUNT(DISTINCT license_key) as dau FROM analytics_events WHERE timestamp::date = CURRENT_DATE")
        dau = cur.fetchone()['dau']

        # MAU — активных за 30 дней
        cur.execute("SELECT COUNT(DISTINCT license_key) as mau FROM analytics_events WHERE timestamp > NOW() - INTERVAL '30 days'")
        mau = cur.fetchone()['mau']

        # Средняя сессия
        cur.execute("SELECT AVG(session_duration_minutes) as avg_session FROM analytics_events WHERE event_type='session_end'")
        avg_session = round(cur.fetchone()['avg_session'] or 0, 1)

        # Retention 7 дней
        cur.execute("SELECT COUNT(DISTINCT license_key) as ret FROM analytics_events WHERE timestamp > NOW() - INTERVAL '7 days'")
        ret7 = cur.fetchone()['ret']
        retention = round((ret7 / mau * 100) if mau > 0 else 0, 1)

        # График DAU за 30 дней
        cur.execute("SELECT date, active_users FROM analytics_daily ORDER BY date DESC LIMIT 30")
        daily = list(reversed(cur.fetchall()))

        # Feature adoption
        cur.execute("SELECT features_used FROM analytics_events WHERE features_used IS NOT NULL")
        feature_counts = {}
        for row in cur.fetchall():
            try:
                features = json.loads(row['features_used'])
                for f in features:
                    feature_counts[f] = feature_counts.get(f, 0) + 1
            except Exception:
                pass

        # Platform breakdown
        cur.execute("SELECT platforms_used FROM analytics_events WHERE platforms_used IS NOT NULL")
        platform_counts = {}
        for row in cur.fetchall():
            try:
                platforms = json.loads(row['platforms_used'])
                for p in platforms:
                    platform_counts[p] = platform_counts.get(p, 0) + 1
            except Exception:
                pass

        # Топ клиенты
        cur.execute("""
            SELECT license_key,
                   COUNT(*) as sessions,
                   SUM(messages_count) as total_msgs,
                   MAX(peak_viewers) as peak_viewers
            FROM analytics_events
            GROUP BY license_key
            ORDER BY total_msgs DESC LIMIT 10
        """)
        top_clients = cur.fetchall()

        # Суммарный охват
        cur.execute("SELECT SUM(peak_viewers) as total_reach, AVG(avg_viewers) as avg_viewers FROM analytics_events WHERE peak_viewers > 0")
        reach = cur.fetchone()
        conn.close()

        return render_template_string(ANALYTICS_DASHBOARD_HTML,
            dau=dau, mau=mau, avg_session=avg_session, retention=retention,
            daily=daily, feature_counts=feature_counts, platform_counts=platform_counts,
            top_clients=top_clients, reach=reach
        )
    except Exception as e:
        return f"Ошибка: {e}", 500

@app.route('/analytics/report')
def analytics_report():
    password = request.args.get('password', '')
    if password != ADMIN_PASSWORD:
        return jsonify({'error': 'Unauthorized'}), 401
    period = request.args.get('period', 'month')
    interval = '30 days' if period == 'month' else '7 days'
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({'error': 'DB unavailable'}), 500
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(f"SELECT COUNT(DISTINCT license_key) as mau FROM analytics_events WHERE timestamp > NOW() - INTERVAL '{interval}'")
        mau = cur.fetchone()['mau']

        cur.execute(f"SELECT AVG(active_users) as dau_avg FROM analytics_daily WHERE date > CURRENT_DATE - INTERVAL '{interval}'")
        dau_avg = round(cur.fetchone()['dau_avg'] or 0, 1)

        cur.execute(f"SELECT COUNT(DISTINCT license_key) as ret7 FROM analytics_events WHERE timestamp > NOW() - INTERVAL '7 days'")
        ret7 = cur.fetchone()['ret7']

        cur.execute(f"SELECT COUNT(DISTINCT license_key) as ret30 FROM analytics_events WHERE timestamp > NOW() - INTERVAL '30 days'")
        ret30 = cur.fetchone()['ret30']

        cur.execute(f"SELECT SUM(session_duration_minutes) as total_mins FROM analytics_events WHERE timestamp > NOW() - INTERVAL '{interval}'")
        total_hours = round((cur.fetchone()['total_mins'] or 0) / 60, 1)

        cur.execute(f"SELECT SUM(messages_count) as msgs FROM analytics_events WHERE timestamp > NOW() - INTERVAL '{interval}'")
        total_messages = cur.fetchone()['msgs'] or 0

        cur.execute(f"SELECT SUM(donations_count) as don FROM analytics_events WHERE timestamp > NOW() - INTERVAL '{interval}'")
        total_donations = cur.fetchone()['don'] or 0

        cur.execute(f"SELECT platforms_used FROM analytics_events WHERE timestamp > NOW() - INTERVAL '{interval}'")
        platform_counts = {}
        for row in cur.fetchall():
            try:
                for p in json.loads(row['platforms_used']):
                    platform_counts[p] = platform_counts.get(p, 0) + 1
            except Exception:
                pass
        total_p = sum(platform_counts.values()) or 1
        platforms_pct = {k: f"{round(v/total_p*100)}%" for k, v in platform_counts.items()}

        cur.execute(f"SELECT features_used FROM analytics_events WHERE timestamp > NOW() - INTERVAL '{interval}'")
        feature_counts = {}
        for row in cur.fetchall():
            try:
                for f in json.loads(row['features_used']):
                    feature_counts[f] = feature_counts.get(f, 0) + 1
            except Exception:
                pass
        total_f = sum(feature_counts.values()) or 1
        features_pct = {k: f"{round(v/total_f*100)}%" for k, v in feature_counts.items()}

        cur.execute(f"""
            SELECT license_key, MAX(peak_viewers) as peak, AVG(avg_viewers) as avg
            FROM analytics_events WHERE peak_viewers > 0 AND timestamp > NOW() - INTERVAL '{interval}'
            GROUP BY license_key ORDER BY peak DESC LIMIT 5
        """)
        top_streamers = [{"license": f"****-{r['license_key'][-4:]}", "peak": r['peak'], "avg": round(r['avg'] or 0)} for r in cur.fetchall()]

        cur.execute(f"SELECT SUM(peak_viewers) as reach FROM analytics_events WHERE timestamp > NOW() - INTERVAL '{interval}'")
        total_reach = cur.fetchone()['reach'] or 0

        conn.close()
        return jsonify({
            "period": period,
            "total_active_users": mau,
            "daily_active_users_avg": dau_avg,
            "retention_7d": f"{round(ret7/mau*100) if mau else 0}%",
            "retention_30d": f"{round(ret30/mau*100) if mau else 0}%",
            "total_streams_hours": total_hours,
            "total_messages_processed": total_messages,
            "total_donations_processed": total_donations,
            "platforms": platforms_pct,
            "features": features_pct,
            "audience_reach": {
                "total_reach": total_reach,
                "top_streamers": top_streamers
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
        
ANALYTICS_DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>TwitchFarm Analytics</title>
    <meta charset="utf-8">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #0e0e10; color: #eee; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #9146FF; }
        .cards { display: flex; gap: 20px; margin: 20px 0; }
        .card { background: #1f1f23; padding: 20px; border-radius: 10px; flex: 1; text-align: center; }
        .card .value { font-size: 36px; font-weight: bold; color: #9146FF; }
        .card .label { color: #adadb8; font-size: 14px; margin-top: 5px; }
        .charts { display: flex; gap: 20px; margin: 20px 0; }
        .chart-box { background: #1f1f23; padding: 20px; border-radius: 10px; flex: 1; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #333; font-size: 13px; }
        th { color: #9146FF; }
        .export-btn { background: #9146FF; color: white; border: none; padding: 12px 24px;
                      border-radius: 8px; cursor: pointer; font-size: 15px; margin-top: 20px; }
    </style>
</head>
<body>
<div class="container">
    <h1>📊 TwitchFarm Analytics</h1>
    <div class="cards">
        <div class="card"><div class="value">{{ dau }}</div><div class="label">DAU (сегодня)</div></div>
        <div class="card"><div class="value">{{ mau }}</div><div class="label">MAU (30 дней)</div></div>
        <div class="card"><div class="value">{{ retention }}%</div><div class="label">Retention 7д</div></div>
        <div class="card"><div class="value">{{ avg_session }}м</div><div class="label">Средняя сессия</div></div>
        <div class="card"><div class="value">{{ reach.total_reach or 0 }}</div><div class="label">Суммарный охват</div></div>
    </div>

    <div class="chart-box" style="margin: 20px 0;">
        <h3>DAU за 30 дней</h3>
        <canvas id="dauChart" height="80"></canvas>
    </div>

    <div class="charts">
        <div class="chart-box">
            <h3>Feature Adoption</h3>
            <canvas id="featureChart"></canvas>
        </div>
        <div class="chart-box">
            <h3>Platform Breakdown</h3>
            <canvas id="platformChart"></canvas>
        </div>
    </div>

    <div class="chart-box" style="margin: 20px 0;">
        <h3>Топ клиенты по активности</h3>
        <table>
            <thead><tr><th>Ключ</th><th>Сессий</th><th>Сообщений</th><th>Пик зрителей</th></tr></thead>
            <tbody>
            {% for c in top_clients %}
            <tr>
                <td>****-{{ c.license_key[-4:] }}</td>
                <td>{{ c.sessions }}</td>
                <td>{{ c.total_msgs or 0 }}</td>
                <td>{{ c.peak_viewers or 0 }}</td>
            </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <button class="export-btn" onclick="window.location='/analytics/report?password={{ request.args.get(\'password\',\'\') }}&period=month'">
        📥 Экспорт отчёта для рекламодателей
    </button>
</div>
<script>
    new Chart(document.getElementById('dauChart'), {
        type: 'line',
        data: {
            labels: [{% for d in daily %}'{{ d.date }}'{% if not loop.last %},{% endif %}{% endfor %}],
            datasets: [{ label: 'DAU', data: [{% for d in daily %}{{ d.active_users }}{% if not loop.last %},{% endif %}{% endfor %}],
                borderColor: '#9146FF', backgroundColor: 'rgba(145,70,255,0.1)', fill: true, tension: 0.4 }]
        },
        options: { plugins: { legend: { labels: { color: '#eee' } } }, scales: { x: { ticks: { color: '#aaa' } }, y: { ticks: { color: '#aaa' } } } }
    });
    new Chart(document.getElementById('featureChart'), {
        type: 'pie',
        data: {
            labels: [{% for k,v in feature_counts.items() %}'{{ k }}'{% if not loop.last %},{% endif %}{% endfor %}],
            datasets: [{ data: [{% for k,v in feature_counts.items() %}{{ v }}{% if not loop.last %},{% endif %}{% endfor %}],
                backgroundColor: ['#9146FF','#5865F2','#F97316','#2ecc71','#e74c3c','#3498db'] }]
        },
        options: { plugins: { legend: { labels: { color: '#eee' } } } }
    });
    new Chart(document.getElementById('platformChart'), {
        type: 'bar',
        data: {
            labels: [{% for k,v in platform_counts.items() %}'{{ k }}'{% if not loop.last %},{% endif %}{% endfor %}],
            datasets: [{ label: 'Использований', data: [{% for k,v in platform_counts.items() %}{{ v }}{% if not loop.last %},{% endif %}{% endfor %}],
                backgroundColor: '#9146FF' }]
        },
        options: { plugins: { legend: { labels: { color: '#eee' } } }, scales: { x: { ticks: { color: '#aaa' } }, y: { ticks: { color: '#aaa' } } } }
    });
</script>
</body>
</html>
"""

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
