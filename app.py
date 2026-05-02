import os
import sqlite3
import secrets
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from whitenoise import WhiteNoise


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BASE_DIR, "instance", "jbrow.sqlite3")
OPENING_HOUR = 8
CLOSING_HOUR = 17
SERVICE_DURATION_MINUTES = 60
AVAILABLE_TIMES = [f"{hour:02d}:00" for hour in range(OPENING_HOUR, CLOSING_HOUR)]


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
    app.config["DATABASE"] = os.environ.get("DATABASE_URL", DATABASE)
    app.wsgi_app = WhiteNoise(
        app.wsgi_app,
        root=os.path.join(BASE_DIR, "static"),
        prefix="static/",
        max_age=31536000,
    )

    os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)

    @app.before_request
    def before_request():
        g.db = get_db()
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)

    @app.teardown_request
    def teardown_request(_exception=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.context_processor
    def inject_globals():
        return {
            "csrf_token": session.get("csrf_token", ""),
            "available_times": AVAILABLE_TIMES,
            "opening_hours_label": "08:00 - 17:00",
            "current_year": datetime.now().year,
            "logo_path": "images/logo.jpeg",
        }

    @app.route("/")
    def index():
        services = query_all(
            "SELECT * FROM services WHERE active = 1 ORDER BY id LIMIT 3"
        )
        testimonials = [
            {
                "name": "Sarah M.",
                "text": "Un résultat précis, élégant et naturel. L'accueil est aussi premium que la prestation.",
            },
            {
                "name": "Amina D.",
                "text": "Mes sourcils sont parfaitement structurés. Je recommande les yeux fermés.",
            },
            {
                "name": "Claire B.",
                "text": "Un institut raffiné, une vraie attention aux détails et un rendu superbe.",
            },
        ]
        return render_template("index.html", services=services, testimonials=testimonials)

    @app.route("/prestations")
    def services():
        services_list = query_all(
            "SELECT * FROM services WHERE active = 1 ORDER BY id"
        )
        return render_template("services.html", services=services_list)

    @app.route("/reservation", methods=["GET", "POST"])
    def booking():
        services_list = query_all(
            "SELECT * FROM services WHERE active = 1 ORDER BY id"
        )

        selected_service_id = request.args.get("service_id", type=int)
        if request.method == "POST":
            validate_csrf()
            form = clean_booking_form(request.form)
            selected_service_id = form["service_id"]
            errors = validate_booking(form)

            if errors:
                for error in errors:
                    flash(error, "danger")
                return (
                    render_template(
                        "booking.html",
                        services=services_list,
                        selected_service_id=selected_service_id,
                        form=form,
                        min_date=date.today().isoformat(),
                    ),
                    400,
                )

            try:
                appointment_id = create_appointment(form)
            except sqlite3.IntegrityError:
                flash(
                    "Ce créneau vient d'être réservé. Merci de choisir une autre heure.",
                    "warning",
                )
                return redirect(
                    url_for(
                        "booking",
                        service_id=form["service_id"],
                        date=form["appointment_date"],
                    )
                )

            return redirect(url_for("confirmation", appointment_id=appointment_id))

        return render_template(
            "booking.html",
            services=services_list,
            selected_service_id=selected_service_id,
            form={"appointment_date": request.args.get("date", default_booking_date())},
            min_date=date.today().isoformat(),
        )

    @app.route("/confirmation/<int:appointment_id>")
    def confirmation(appointment_id):
        appointment = query_one(
            """
            SELECT appointments.*, services.name AS service_name,
                   services.duration_minutes, services.price
            FROM appointments
            JOIN services ON services.id = appointments.service_id
            WHERE appointments.id = ?
            """,
            (appointment_id,),
        )
        if appointment is None:
            abort(404)
        return render_template("confirmation.html", appointment=appointment)

    @app.route("/contact")
    def contact():
        return render_template("contact.html")

    @app.route("/api/slots")
    def api_slots():
        requested_date = request.args.get("date", "")
        service_id = request.args.get("service_id", type=int)
        if not service_id or query_one(
            "SELECT id FROM services WHERE id = ? AND active = 1", (service_id,)
        ) is None:
            return jsonify({"slots": [], "error": "Prestation invalide."}), 400

        try:
            parsed_date = datetime.strptime(requested_date, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"slots": [], "error": "Date invalide."}), 400

        slots = get_available_slots(parsed_date)
        return jsonify({"slots": slots})

    @app.route("/admin", methods=["GET", "POST"])
    def admin_login():
        if session.get("admin_authenticated"):
            return redirect(url_for("admin_dashboard"))

        if request.method == "POST":
            validate_csrf()
            password = request.form.get("password", "")
            admin_password_hash = os.environ.get("ADMIN_PASSWORD_HASH")
            admin_password = os.environ.get("ADMIN_PASSWORD", "change-me")

            authenticated = False
            if admin_password_hash:
                authenticated = check_password_hash(admin_password_hash, password)
            else:
                authenticated = password == admin_password

            if authenticated:
                session["admin_authenticated"] = True
                flash("Bienvenue dans l'espace administration.", "success")
                return redirect(url_for("admin_dashboard"))

            flash("Mot de passe incorrect.", "danger")

        return render_template("admin_login.html")

    @app.route("/admin/dashboard")
    @admin_required
    def admin_dashboard():
        filter_date = request.args.get("date", "").strip()
        params = []
        where_clause = ""
        if filter_date:
            try:
                datetime.strptime(filter_date, "%Y-%m-%d")
                where_clause = "WHERE appointments.appointment_date = ?"
                params.append(filter_date)
            except ValueError:
                flash("Filtre de date invalide.", "warning")
                filter_date = ""

        appointments = query_all(
            f"""
            SELECT appointments.*, services.name AS service_name
            FROM appointments
            JOIN services ON services.id = appointments.service_id
            {where_clause}
            ORDER BY appointments.appointment_date ASC, appointments.appointment_time ASC
            """,
            tuple(params),
        )
        return render_template(
            "admin_dashboard.html",
            appointments=appointments,
            filter_date=filter_date,
        )

    @app.route("/admin/appointments/<int:appointment_id>/cancel", methods=["POST"])
    @admin_required
    def admin_cancel_appointment(appointment_id):
        validate_csrf()
        g.db.execute(
            "UPDATE appointments SET status = 'cancelled' WHERE id = ?",
            (appointment_id,),
        )
        g.db.commit()
        flash("Rendez-vous annulé.", "success")
        return redirect(url_for("admin_dashboard", date=request.form.get("date", "")))

    @app.route("/admin/appointments/<int:appointment_id>/delete", methods=["POST"])
    @admin_required
    def admin_delete_appointment(appointment_id):
        validate_csrf()
        g.db.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))
        g.db.commit()
        flash("Rendez-vous supprimé.", "success")
        return redirect(url_for("admin_dashboard", date=request.form.get("date", "")))

    @app.route("/admin/logout", methods=["POST"])
    @admin_required
    def admin_logout():
        validate_csrf()
        session.pop("admin_authenticated", None)
        flash("Vous êtes déconnecté.", "info")
        return redirect(url_for("index"))

    @app.cli.command("init-db")
    def init_db_command():
        init_db()
        print("Base SQLite initialisée avec les prestations JBROW.")

    with app.app_context():
        init_db()

    return app


def get_db():
    db_path = current_database_path()
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def current_database_path():
    value = os.environ.get("DATABASE_URL", DATABASE)
    if value.startswith("sqlite:///"):
        return value.replace("sqlite:///", "", 1)
    return value


def query_all(sql, params=()):
    return g.db.execute(sql, params).fetchall()


def query_one(sql, params=()):
    return g.db.execute(sql, params).fetchone()


def init_db():
    db = sqlite3.connect(current_database_path())
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL DEFAULT 60,
            price TEXT NOT NULL,
            image TEXT,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT NOT NULL,
            service_id INTEGER NOT NULL,
            appointment_date TEXT NOT NULL,
            appointment_time TEXT NOT NULL,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'booked',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (service_id) REFERENCES services (id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_booked_slot
        ON appointments (appointment_date, appointment_time)
        WHERE status = 'booked';
        """
    )

    existing = db.execute("SELECT COUNT(*) AS total FROM services").fetchone()["total"]
    if existing == 0:
        db.executemany(
            """
            INSERT INTO services
                (name, description, duration_minutes, price, image, active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            [
                (
                    "Micro Shading",
                    "Restructuration et pigmentation semi-permanente des sourcils pour un rendu poudré, net et élégant.",
                    60,
                    "Sur devis",
                    "images/placeholder-micro-shading.svg",
                ),
                (
                    "Esthétique",
                    "Soins beauté personnalisés pour sublimer le visage et le regard.",
                    60,
                    "Prix à confirmer",
                    "images/placeholder-esthetique.svg",
                ),
                (
                    "Beauté du regard",
                    "Prestations dédiées à l'harmonie du regard.",
                    60,
                    "Prix à confirmer",
                    "images/placeholder-regard.svg",
                ),
            ],
        )
    db.commit()
    db.close()


def clean_booking_form(form):
    return {
        "first_name": form.get("first_name", "").strip(),
        "last_name": form.get("last_name", "").strip(),
        "phone": form.get("phone", "").strip(),
        "email": form.get("email", "").strip().lower(),
        "service_id": form.get("service_id", type=int),
        "appointment_date": form.get("appointment_date", "").strip(),
        "appointment_time": form.get("appointment_time", "").strip(),
        "notes": form.get("notes", "").strip(),
    }


def validate_booking(form):
    errors = []
    required_fields = {
        "first_name": "Le prénom est obligatoire.",
        "last_name": "Le nom est obligatoire.",
        "phone": "Le téléphone est obligatoire.",
        "email": "L'email est obligatoire.",
        "service_id": "Merci de choisir une prestation.",
        "appointment_date": "Merci de choisir une date.",
        "appointment_time": "Merci de choisir un créneau.",
    }
    for field, message in required_fields.items():
        if not form.get(field):
            errors.append(message)

    if form.get("email") and "@" not in form["email"]:
        errors.append("Merci d'indiquer une adresse email valide.")

    service = None
    if form.get("service_id"):
        service = query_one(
            "SELECT * FROM services WHERE id = ? AND active = 1",
            (form["service_id"],),
        )
        if service is None:
            errors.append("La prestation sélectionnée n'est pas disponible.")

    parsed_date = None
    if form.get("appointment_date"):
        try:
            parsed_date = datetime.strptime(form["appointment_date"], "%Y-%m-%d").date()
            if parsed_date < date.today():
                errors.append("Impossible de réserver une date passée.")
        except ValueError:
            errors.append("Le format de la date est invalide.")

    if form.get("appointment_time") and form["appointment_time"] not in AVAILABLE_TIMES:
        errors.append("Ce créneau horaire n'est pas proposé.")

    if service and service["duration_minutes"] != SERVICE_DURATION_MINUTES:
        errors.append("Cette prestation n'a pas une durée compatible avec le planning.")

    if parsed_date and form.get("appointment_time"):
        if form["appointment_time"] not in get_available_slots(parsed_date):
            errors.append("Ce créneau n'est plus disponible.")

    return errors


def get_available_slots(parsed_date):
    if parsed_date < date.today():
        return []

    now = datetime.now()
    booked = query_all(
        """
        SELECT appointment_time
        FROM appointments
        WHERE appointment_date = ? AND status = 'booked'
        """,
        (parsed_date.isoformat(),),
    )
    booked_times = {row["appointment_time"] for row in booked}
    slots = []
    for slot in AVAILABLE_TIMES:
        slot_time = datetime.strptime(slot, "%H:%M").time()
        if parsed_date == now.date() and slot_time <= now.time():
            continue
        if slot not in booked_times:
            slots.append(slot)
    return slots


def default_booking_date():
    now = datetime.now()
    if now.time() >= datetime.strptime(AVAILABLE_TIMES[0], "%H:%M").time():
        return (date.today() + timedelta(days=1)).isoformat()
    return date.today().isoformat()


def create_appointment(form):
    cursor = g.db.execute(
        """
        INSERT INTO appointments
            (first_name, last_name, phone, email, service_id,
             appointment_date, appointment_time, notes, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'booked')
        """,
        (
            form["first_name"],
            form["last_name"],
            form["phone"],
            form["email"],
            form["service_id"],
            form["appointment_date"],
            form["appointment_time"],
            form["notes"],
        ),
    )
    g.db.commit()
    return cursor.lastrowid


def validate_csrf():
    token = request.form.get("csrf_token", "")
    if not token or token != session.get("csrf_token"):
        abort(400, "Jeton CSRF invalide.")


def admin_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if not session.get("admin_authenticated"):
            flash("Merci de vous connecter pour accéder à l'administration.", "warning")
            return redirect(url_for("admin_login"))
        return view(**kwargs)

    return wrapped_view


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
