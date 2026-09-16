import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, redirect, render_template, url_for

from db.db import get_conn, get_open_postings, mark_linkedin_seen

app = Flask(__name__)


@app.route("/")
def index():
    conn = get_conn()
    try:
        postings = get_open_postings(conn)
    finally:
        conn.close()
    return render_template("index.html", postings=postings)


@app.route("/mark_seen/<int:posting_id>", methods=["POST"])
def mark_seen(posting_id):
    conn = get_conn()
    try:
        mark_linkedin_seen(conn, posting_id)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5050)
