from flask import Flask, request, redirect, render_template
from wtforms import Form, StringField, DateField, validators, ValidationError
import psycopg2
from moneyed import Money, EUR
from moneyed.l10n import format_money
from flask_bootstrap import Bootstrap5
import pyodbc
import os
import yaml
from wtforms.fields.simple import HiddenField

app = Flask(__name__)
bootstrap = Bootstrap5(app)

db_type = os.getenv('DB_TYPE')


def get_connection():
    conn = None
    if db_type == 'postgres':
        database = os.getenv('DB_DATABASE')
        user = os.getenv('DB_USERNAME')
        password = os.getenv('DB_PASSWORD')
        host = os.getenv('DB_HOST')
        port = os.getenv('DB_PORT')
        conn = psycopg2.connect(database=database, user=user, password=password, host=host, port=port)
    if db_type == 'mssql':
        SERVER = os.getenv('DB_SERVER')
        DATABASE = os.getenv('DB_DATABASE')
        USERNAME = os.getenv('DB_USERNAME')
        PASSWORD = os.getenv('DB_PASSWORD')

        connection_string = f'DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={SERVER};DATABASE={DATABASE};UID={USERNAME};PWD={PASSWORD}'

        conn = pyodbc.connect(connection_string)
    else:
        config = None
        with open('config.yaml') as f:
            config = yaml.safe_load(f)
        SERVER = config['database'][1]['server']
        DATABASE = config['database'][1]['database']
        USERNAME = config['database'][1]['username']
        PASSWORD = config['database'][1]['password']

        connection_string = f'DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={SERVER};DATABASE={DATABASE};UID={USERNAME};PWD={PASSWORD}'

        conn = pyodbc.connect(connection_string)

    return conn


def to_float(a_str):
    return float(a_str.replace(',', '').replace('$', ''))


def get_group_members(group_name):
    sql = '''
        SELECT m.name from [wg-haka].mitglieder m JOIN [wg-haka].mitglied_in mi on m.id = mi.mitglied_id JOIN [wg-haka].gruppen g on g.id = mi.gruppe_id 
        WHERE g.name = ?
    '''

    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(sql, group_name)
        results = cursor.fetchall()
        members = []
        for result in results:
            members.append(result[0])

        return members
    except psycopg2.DatabaseError as error:
        print(error)
    finally:
        if connection is not None:
            connection.close()


def get_group_expenses(group_name):
    sql = '''
        SELECT a.wer, a.was, a.wann, a.wieviel, a.id FROM [wg-haka].ausgaben a JOIN [wg-haka].gruppen g on a.gruppe_id = g.id where g.name = ?
    '''

    connection = None
    try:

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(sql, group_name)
        results = cursor.fetchall()
        group_expenses = []
        for result in results:
            group_expenses.append({'id': result[4], 'wer': result[0], 'was': result[1], 'wann': result[2],
                                   'wieviel': Money(amount=result[3], currency=EUR)})

        return group_expenses

    except psycopg2.DatabaseError as error:
        print(error)
    finally:
        if connection is not None:
            connection.close()


def get_group_overall_expenses(group_name):
    sql = '''
        SELECT sum(a.wieviel) from [wg-haka].ausgaben a JOIN [wg-haka].gruppen g on a.gruppe_id = g.id where g.name = ?
    '''

    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(sql, group_name)
        results = cursor.fetchone()
        return Money(amount=results[0], currency=EUR)
    except psycopg2.DatabaseError as error:
        print(error)
    finally:
        if connection is not None:
            connection.close()


def get_group_overview(group_name):
    sql = '''
        with anzahl_gruppe as (select count(*) mitglieder
                       from [wg-haka].mitglied_in mi
                                join [wg-haka].gruppen g on mi.gruppe_id = g.id
                       where g.name = ?)
        select sum(a1.wieviel),
               a1.wer,
               g.gesamt,
               g.gesamt / 5 as                            durchschnitt,
               sum(a1.wieviel) - g.gesamt / ag.mitglieder as auslagen
        from [wg-haka].ausgaben a1
                 JOIN (SELECT sum(wieviel) gesamt, a2.gruppe_id as gruppe_id from [wg-haka].ausgaben a2 group by a2.gruppe_id) g
                      on a1.gruppe_id = g.gruppe_id
                JOIN [wg-haka].gruppen gr on a1.gruppe_id = gr.id
                 JOIN anzahl_gruppe ag on 1 = 1
        WHERE gr.name = ?
        group by wer, g.gesamt, ag.mitglieder
    '''

    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(sql, group_name, group_name)

        results = cursor.fetchall()
        group_overview = []
        for result in results:
            group_overview.append({
                'sum': Money(amount=result[0], currency=EUR),
                'wer': result[1],
                'gesamt': Money(amount=result[2], currency=EUR),
                'durchschnitt': Money(amount=result[3], currency=EUR),
                'auslagen': Money(amount=result[4], currency=EUR)
            })

        return group_overview

    except psycopg2.DatabaseError as error:
        print(error)
    finally:
        if connection is not None:
            connection.close()


@app.route('/')
def home():
    return render_template('home.html')


@app.route('/groups/create')
def create_group():
    return render_template('create_group.html')


@app.route('/groups', methods=['POST', 'GET'])
def groups():
    connection = None
    try:

        connection = get_connection()
        cursor = connection.cursor()
        if request.method == 'POST':
            cursor.execute("INSERT INTO [wg-haka].gruppen (name) VALUES (?)", request.form['name'])
            connection.commit()
        cursor.execute("SELECT * from [wg-haka].gruppen")
        gruppen = cursor.fetchall()
        return render_template('groups.html', gruppen=gruppen)
    except psycopg2.DatabaseError as error:
        print(error)
    finally:
        if connection is not None:
            connection.close()


def berechne_ausgleichszahlung(group_overview):
    max_entry = max(group_overview, key=lambda entry: entry['auslagen'])
    min_entry = min(group_overview, key=lambda entry: entry['auslagen'])
    a = []
    for entry in group_overview:
        a.append({
            'wer': entry['wer'],
            'sum': entry['sum'],
            'auslagen': entry['auslagen'],
            'zahlungen': [],
            'erhalten': []
        })
    print("Max Entry is: ", max_entry)
    print("Min Entry is: ", min_entry)
    delta = 0.01
    counter = 0
    while (counter <= 1000):
        max_entry = max(a, key=lambda entry: entry['auslagen'])
        min_entry = min(a, key=lambda entry: entry['auslagen'])

        if abs(min_entry['auslagen'].amount) <= abs(max_entry['auslagen'].amount):
            betrag = Money(amount=abs(min_entry['auslagen'].amount), currency='EUR')
        else:
            betrag = Money(amount=abs(max_entry['auslagen'].amount), currency='EUR')

        if betrag.amount > delta:
            min_entry['zahlungen'].append({
                'an': max_entry['wer'],
                'wieviel': betrag
            })
            max_entry['erhalten'].append({
                'von': min_entry['wer'],
                'wieviel': betrag
            })
            min_entry['sum'] += betrag
            min_entry['auslagen'] += betrag
            max_entry['sum'] -= betrag
            max_entry['auslagen'] -= betrag

        counter += 1

    return a


def money_str(money):
    return format_money(money, locale='de')


app.jinja_env.globals.update(money_str=money_str)


def user_exists(username):
    sql = '''
        SELECT count(*) FROM [wg-haka].mitglieder where name = ?
    '''

    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(sql, username)
        result = cursor.fetchone()
        print(result)
        if result[0] == 1:
            return True
        else:
            return False

    except psycopg2.DatabaseError as error:
        print(error)
    finally:
        if connection is not None:
            connection.close()


class SpendingForm(Form):
    method = HiddenField('_method')
    spending_id = HiddenField('_spending_id')
    wer = StringField('Wer', [validators.DataRequired(), validators.Length(min=3, max=256)])
    was = StringField('Was', [validators.Optional(), validators.Length(min=3, max=256)])
    wann = DateField('Wann', validators=[validators.DataRequired()])
    wieviel = StringField('Wieviel', validators=[
        validators.DataRequired(),
        validators.regexp(r"^(?>(?>\d*)(?>[\,\.])(?>\d{1,2})|(?>\d+))$",
                          message='Gib bitte einen Betrag ein')
    ])

    def validate_wer(form, field):
        if not user_exists(field.data):
            raise ValidationError('Name ist nicht vorhanden.')


@app.route('/groups/<group_name>/create_spending')
def create_spending(group_name):
    form = SpendingForm(request.form, meta={'locales': ['de_DE', 'de']})
    return render_template('create_spending.html', group=group_name, form=form)


def get_spending(spending):
    sql = '''
        SELECT wer, was, wann, wieviel from [wg-haka].ausgaben where id = ?
    '''

    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(sql, spending)
        result = cursor.fetchone()

        return {
            'wer': result[0],
            'was': result[1],
            'wann': result[2],
            'wieviel': result[3]
        }
    except psycopg2.DatabaseError as error:
        print(error)
        connection.rollback()
    finally:
        if connection is not None:
            connection.close()

def truncate(f, n):
    '''Truncates/pads a float f to n decimal places without rounding'''
    s = '{}'.format(f)
    if 'e' in s or 'E' in s:
        return '{0:.{1}f}'.format(f, n)
    i, p, d = s.partition('.')
    return '.'.join([i, (d+'0'*n)[:n]])

@app.route('/groups/<group_name>/spendings/<spending>')
def update_spending(group_name, spending):
    form = SpendingForm(request.form, meta={'locales': ['de_DE', 'de']})
    result = get_spending(spending)
    form.wer.data = result['wer']
    form.was.data = result['was']
    form.wann.data = result['wann']
    form.wieviel.data = truncate(result['wieviel'], 2)
    form.spending_id.data = spending
    form.method.data = "PUT"
    return render_template('spending.html', group=group_name, spending=spending, form=form)


def update_spending(wer, was, wieviel, wann, spending_id):
    sql = '''
        UPDATE [wg-haka].ausgaben
        SET wer = ?, was = ?, wieviel = ?, wann = ?
        WHERE id = ?
    '''

    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(sql, wer, was, wieviel, wann, spending_id)
        connection.commit()
    except psycopg2.DatabaseError as error:
        print(error)
        connection.rollback()
    finally:
        if connection is not None:
            connection.close()


def add_spending(wer, was, wieviel, wann, gruppe):
    sql_gruppe_id = '''
        SELECT id from [wg-haka].gruppen where name = ?
    '''
    sql = '''
        INSERT INTO [wg-haka].ausgaben (wer, was, wann, wieviel, gruppe_id) VALUES (?,?,?,?,?) 
    '''

    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(sql_gruppe_id, gruppe)
        result = cursor.fetchone()
        gruppe_id = result[0]
        if result is not None:
            cursor.execute(sql, wer, was, wann, wieviel, gruppe_id)
            connection.commit()
    except psycopg2.DatabaseError as error:
        print(error)
        connection.rollback()
    finally:
        if connection is not None:
            connection.close()


@app.route('/groups/<group_name>/spending', methods=['POST', 'GET'])
def spending(group_name):
    form = SpendingForm(request.form, meta={'locales': ['de_DE', 'de']})
    if request.method == 'POST' and form.validate():
        if form.method.data == 'PUT':
            update_spending(form.wer.data, form.was.data, form.wieviel.data, form.wann.data, form.spending_id.data)
        else:
            add_spending(form.wer.data, form.was.data, form.wieviel.data, form.wann.data, group_name)

        return redirect('/groups/' + group_name, code=302)
    else:
        print('Fehler bei der Eingabe')
        return render_template('create_spending.html', group=group_name, form=form)


@app.route('/groups/<group_name>')
def group(group_name):
    group_expenses = get_group_expenses(group_name)
    group_overall_expenses = get_group_overall_expenses(group_name)
    group_overview = get_group_overview(group_name)
    group_members = get_group_members(group_name)

    ausgleichszahlungen = berechne_ausgleichszahlung(group_overview)

    group = {
        'name': group_name,
        'members': group_members,
        'gesamtausgaben': group_overall_expenses,
        'ueberblick': group_overview,
        'ausgaben': group_expenses,
        'ausgleichszahlungen': ausgleichszahlungen
    }

    return render_template('group.html', group=group)
