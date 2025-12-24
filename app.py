import os
import random
from flask import Flask, render_template, request, flash, redirect, url_for, session
from flask_mail import Mail, Message
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, UTC
from markupsafe import escape

"""
TODO
1. Add time-editing functionality to AvailableTimes database.
"""

# Flask app setup
app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

# SQLAlchemy setup
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URI')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class AvailableTimes(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    is_booked = db.Column(db.Boolean, default=False)

# SendGrid API setup
app.config['MAIL_SERVER'] = 'smtp.sendgrid.net'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'apikey'
app.config['MAIL_PASSWORD'] = os.environ.get('SENDGRID_API_KEY') # change these when free trial ends
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER') # change these when free trial ends
mail = Mail(app)


# Email verification
def generate_verification_code():
    """Return a random 6-digit string like '483920'."""
    return f"{random.randint(0, 999999):06d}"

def send_verification_email(code: str, email: str):
    """Sends an email with a verification code."""
    if not session.get("last_email_time") or (datetime.now(UTC) - session["last_email_time"] > timedelta(minutes=2)):
        session["last_email_time"] = datetime.now(UTC)
        msg = Message(f"Verify your email", recipients=[email])
        msg.body = "Verify your email"
        msg.html = f"<p>Hi! Your verification code is <strong>{code}</strong>.</p>"
        mail.send(msg)
        return True
    else:
        return False

def check_verification_code(code):
    """Checks the validity of the 6-digit code string."""
    if not session.get("last_code_time") or (datetime.now(UTC) - session["last_code_time"] > timedelta(seconds=5)):
        session["last_code_time"] = datetime.now(UTC)
        if not session["verification_code"] or not (len(code) == 6 and code.isdigit()) or (session["verification_code"] != code):
            return False 
        else:
            return True
    else:
        return False
    

# Meeting scheduling
def get_available_times():
    """Returns a list of available times from the AvailableTimes table."""
    now = datetime.now()

    # Only return times where is_booked = False and start_time >= now.
    available_times = db.session.execute(db.select(AvailableTimes)
                                         .where(AvailableTimes.is_booked == False)
                                         .where(AvailableTimes.start_time >= now)
                                         .order_by(AvailableTimes.start_time.asc())).scalars().all()

    return available_times

def make_appointment(slot_id, email, name, user_note):
    """Updates the AvailableTimes table and sends emails to me and user of appointment."""

    # Updates the is_booked parameter
    appointment = db.session.get(AvailableTimes, slot_id)
    if appointment is None:
        raise ValueError("Appointment not found.")
    if appointment.is_booked:
        raise ValueError("That time slot is already booked.")
    appointment.is_booked = True
    db.session.commit()

    # Email notifies me and the user. Manually create the Google Calendar invite later.
    safe_note = escape(user_note)
    msg = Message(f"Meeting confirmation", recipients=[email, os.environ.get('APPOINTMENT_RECEIVER')])
    msg.body = "Meeting confirmation"
    msg.html = f"""
                <p>Meeting appointment made for {name} ({email}) from: 
                <strong>{appointment.start_time}</strong> to <strong>{appointment.end_time}</strong>.</p>
                <p><strong>Note: </strong><br>{safe_note}</p>
                """
    mail.send(msg)

def add_time(start_time, end_time):
    """Adds a new time to the AvailableTimes table."""
    new_time = AvailableTimes(start_time=start_time, end_time=end_time)
    db.session.add(new_time)
    db.session.commit()


# Routes
@app.route('/', methods=["GET", "POST"])
def index():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        session["email"] = email
        if not email or (not email.endswith(".harvard.edu") and not email.endswith(".mit.edu")):
            flash("Please enter a valid Harvard/MIT email ending with '.edu'.")
            return redirect(url_for("index"))
        else:
            # Send verification code email + store in session
            verification_code = generate_verification_code()
            session["verification_code"] = verification_code
            
            # Send email using SendGrid
            if send_verification_email(verification_code, email):
                session["email_sent"] = True
                flash("Verification sent.")
                return redirect(url_for("verify"))
            else:
                flash("Please try again later.")
                return redirect(url_for("index"))

    return render_template('index.html')

@app.route('/verify', methods=["GET", "POST"])
def verify():
    if request.method == "POST":
        code = request.form.get("verification_code").strip()

        if check_verification_code(code):
            session["is_verified"] = True
            flash("Email verified!")
            return redirect(url_for("schedule"))
        else:
            flash("Please enter a valid verification code or try again later.")

    return render_template("verify.html")

@app.route('/schedule', methods=["GET", "POST"])
def schedule():
    if not session.get("is_verified"):
        flash("Please verify your email.")
        return redirect(url_for("index"))

    # Obtain available times
    available_times = get_available_times()

    if request.method == "POST":
        slot_id = request.form.get("time_slot", type=int)
        name = request.form.get("name")
        user_note = request.form.get("notes")
        make_appointment(slot_id, session["email"], name, user_note)
        flash(f"Reserved for {name}. See you then!", "success")

    return render_template("schedule.html", available_times=available_times)


# Admin
@app.route('/admin', methods=["GET", "POST"])
def admin():
    if not session.get("is_admin"):
        flash("An unknown error occurred.")
        return redirect(url_for("index"))

    # Handle adding new times
    if request.method == "POST":
        raw_start = request.form.get("start_time")
        raw_end = request.form.get("end_time")
        try:
            start_dt = datetime.strptime(raw_start, "%Y-%m-%dT%H:%M")
            end_dt = datetime.strptime(raw_end, "%Y-%m-%dT%H:%M")
        except (TypeError, ValueError):
            flash("Invalid date/time format.", "danger")
            return redirect(url_for("admin"))

        if end_dt <= start_dt:
            flash("End time must be after start time.", "danger")
            return redirect(url_for("admin"))

        add_time(start_dt, end_dt)

        flash(f"Created a new time slot from {start_dt} to {end_dt}!","success")

    return render_template("admin.html")

@app.route("/admin/login", methods=["POST"])
def admin_login():
    pw = (request.form.get("admin_password") or "").strip()
    expected = os.environ.get("ADMIN_PASSWORD", "")

    if not expected:
        flash("Admin login is not configured.")
        return redirect(url_for("index"))

    if pw == expected:
        session["is_admin"] = True
        return redirect(url_for("admin"))

    flash("Incorrect admin password.")
    return redirect(url_for("index"))

@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    flash("Logged out.")
    return redirect(url_for("index"))


# Run the app and create database
if __name__ == '__main__':
    app.run(debug=True)
