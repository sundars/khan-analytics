import os
import simplejson as json
import datetime
import cookie_util
import math
import logging
import urllib, urllib2

from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext import db
from google.appengine.api import mail
from google.appengine.ext import deferred
from google.appengine.runtime import DeadlineExceededError

from oauth_util import OAuthUtil

class PaypalTransaction(db.Model):
    transaction_id = db.StringProperty()
    student_email = db.StringProperty()
    status = db.StringProperty()

class SummerStudent(db.Model):
    email = db.StringProperty()
    applier_email = db.StringProperty()
    application_year = db.StringProperty()
    application_status = db.StringProperty()

    first_name = db.StringProperty()
    last_name = db.StringProperty()
    date_of_birth = db.StringProperty()
    is_female = db.BooleanProperty()
    grade = db.StringProperty()
    school = db.StringProperty()
    address_1 = db.StringProperty()
    address_2 = db.StringProperty()
    city = db.StringProperty()
    state = db.StringProperty()
    zipcode = db.StringProperty()
    country = db.StringProperty()

    parent_email = db.StringProperty()
    parent_relation = db.StringProperty()

    first_choice = db.StringListProperty()
    second_choice = db.StringListProperty()
    third_choice = db.StringListProperty()
    no_choice = db.StringListProperty()
    session_1 = db.StringProperty()
    session_2 = db.StringProperty()
    session_3 = db.StringProperty()

    answer_why = db.TextProperty()
    answer_how = db.TextProperty()

    processing_fee = db.StringProperty()
    processing_fee_paid = db.BooleanProperty()

    extended_care = db.BooleanProperty()
    lunch = db.BooleanProperty()
    
    tuition = db.StringProperty()
    tuition_paid = db.BooleanProperty()

    scholarship_applied = db.BooleanProperty()
    scholarship_granted = db.BooleanProperty()
    scholarship_amount = db.StringProperty()

    self_applied = db.BooleanProperty()

    def to_dict(self):
        return dict([(p, unicode(getattr(self, p))) for p in self.properties()])

class ParentData(db.Model):
    first_name = db.StringProperty()
    last_name = db.StringProperty()
    email = db.StringProperty()
    address_1 = db.StringProperty()
    address_2 = db.StringProperty()
    city = db.StringProperty()
    state = db.StringProperty()
    zipcode = db.StringProperty()
    country = db.StringProperty()
    phone = db.StringProperty()
    comments = db.TextProperty()
    students = db.ListProperty(db.Key)

    def to_dict(self):
        return dict([(p, unicode(getattr(self, p))) for p in self.properties()])

class PaypalIPN(webapp.RequestHandler):
    def post(self):
        self.get()

    def get(self):
        logging.info("Accessing %s" % self.request.path)
        txn_id = self.request.get('txn_id')
        student_email = self.request.get('custom')

        charset = self.request.get('charset')
        parameters = dict((arg, self.request.get(arg).encode(charset)) for arg in self.request.arguments())

        parameters['cmd'] = "_notify-validate"
        req = urllib2.Request("https://www.sandbox.paypal.com/cgi-bin/webscr", urllib.urlencode(parameters))
        req.add_header("Content-type", "application/x-www-form-urlencoded")

        response = urllib2.urlopen(req)
        status = response.read()
        if status == "VERIFIED":
            query = PaypalTransaction.all()
            query.filter('transaction_id = ', txn_id)
            paypal_txn = query.get()

            if paypal_txn is None:
                paypal_txn = PaypalTransaction()
                paypal_txn.transaction_id = txn_id
                paypal_txn.status = "Initiated"

            paypal_txn.student_email = student_email
            paypal_txn.status = parameters['payment_status']

            query = SummerStudent.all()
            query.filter('email = ', paypal_txn.student_email)
            student = query.get()

            if student is None:
                logging.error("Student not found in DB for email <%s>" % student_email)
            else:
                student.processing_fee = parameters['payment_gross']

                if paypal_txn.status == "Completed":
                    student.processing_fee_paid = True
                else:
                    student.processing_fee_paid = False

                student.put()

            paypal_txn.put()
        else:
            logging.error("Paypal did not verify the IPN response transaction id <%s>" % txn_id)

        return

class PaypalAutoReturn(webapp.RequestHandler):
    def post(self):
        self.get()

    def get(self):
        logging.info("Accessing %s" % self.request.path)
        student_email = self.request.get('student_email')
        user_email = self.request.get('user_email')
        txn_id = self.request.get('tx')
        #id_token = "d-bgpj-IRtoq2Fl2wbNQjgjAAWVhnZHlBihznOlZtNnEgcscBdujjOhfA18"
        id_token = "GpWfe9SEzMcEzlQptmLkJn0xLsxUAISHya6-0OZZWkzWayM0AWKT25DyLbG"

        query = PaypalTransaction.all()
        query.filter('transaction_id = ', txn_id)
        paypal_txn = query.get()

        if paypal_txn is None:
            paypal_txn = PaypalTransaction()
            paypal_txn.transaction_id = txn_id
            paypal_txn.student_email = student_email
            paypal_txn.status = "Initiated"

        url = "https://www.sandbox.paypal.com/cgi-bin/webscr"
        values = {
            "cmd" : "_notify-synch",
            "tx" : txn_id,
            "at" : id_token
        }

        try:
            data = urllib.urlencode(values)
            req = urllib2.Request(url, data)
            response = urllib2.urlopen(req)
            output = response.read().split('\n')
        except Exception, e:
            logging.error("Error getting transaction info from Paypal <%s>" % e)
        else:
            query = SummerStudent.all()
            query.filter('email = ', student_email)
            student = query.get()
            if student is None:
                logging.error("Student not found in DB for email <%s>" % student_email)
            else:
                count = len(output) - 1
                paypal_attr = {}
                if output[0] == "SUCCESS":
                    for i in range(1,count):
                        nvp = output[i].split('=')
                        paypal_attr[nvp[0]] = nvp[1]

                    paypal_txn.status = paypal_attr['payment_status']
                    student.processing_fee = paypal_attr['payment_gross']

                    if paypal_txn.status == "Completed":
                        student.processing_fee_paid = True
                    else:
                        student.processing_fee_paid = False
                else:
                    logging.error("Transaction %s for %s didn't succeed" % (txn_id, student_email))
                    student.processing_fee_paid = False

                student.put()

        paypal_txn.put()

        self.redirect("/summer/application-status")

class GetStudent(webapp.RequestHandler):
    def get(self):
        student_email = self.request.get('student_email')
        logging.info("Accessing %s; student %s" % (self.request.path, student_email))
        query = SummerStudent.all()
        query.filter('email = ', student_email)
        student = query.get()
        if student is None:
            output_str = json.dumps(student)
        else:
            output_str = json.dumps(student.to_dict())

        self.response.set_status(200)
        callback = self.request.get('callback')
        if callback:
            self.response.out.write("%s(%s)" % (callback, output_str))
        else:
            self.response.out.write(output_str)

        return

class Process(webapp.RequestHandler):
    def authenticated_response(self, oauth_util):
        admin_test = json.loads(oauth_util.access_resource("/api/v1/user?email=coach@khanacademy.org"))
        if admin_test is None or admin_test['email'] != "coach@khanacademy.org":
            self.response.set_status(401)
            self.response.out.write("Not Authorized")
            return

        user = json.loads(oauth_util.access_resource("/api/v1/user"))
        user_email = user['email']

        template_values = {
                "authenticated" : True,
                "user_email" : user_email,
                "username" : user_email,
                "logged_in" : True,
                "logout_url" : "/logout"
        }

        return template_values

    def get(self):
        template_values = {}
        oauth_util = OAuthUtil()
	token = self.request.get('oauth_token')

        if token:
            oauth_util.get_access_token(self)
	    template_values = self.authenticated_response(oauth_util)
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_key",oauth_util.access_token.key,max_age=2629743))
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_secret",oauth_util.access_token.secret,max_age=2629743))

	else:
            access_token_key = self.request.cookies.get('access_key')
            access_token_secret = self.request.cookies.get('access_secret')
            if access_token_key:
                oauth_util.set_access_token(access_token_key, access_token_secret)
                template_values = self.authenticated_response(oauth_util)

            else:
	        oauth_url = oauth_util.get_request_token_url(self.request.url)

                template_values = {
	            "authenticated" : False,
	            "login_url" : oauth_url
	        }

        self.response.out.write(template.render('summer_process.html', template_values))


class Status(webapp.RequestHandler):
    def authenticated_response(self, oauth_util):
        user = json.loads(oauth_util.access_resource("/api/v1/user"))
        user_email = user['email']
        logging.info("Accessing %s; user %s" % (self.request.path, user_email))

        query = SummerStudent.all()
        query.filter('email = ', user_email)
        student = query.get()

        students = []
        is_parent = False

        if student is None:
            query = ParentData.all()
            query.filter('email = ', user_email)
            parent = query.get()
            if parent is None:
                self.redirect("/summer/application")
                return

            is_parent = True
            for student_key in parent.students:
                students.append(SummerStudent.get(student_key))

        else:
            students.append(student)

        template_values = {
            "authenticated" : True,
            "is_parent" : is_parent,
            "students" : students,
            "user_email" : user_email,
            "username" : user_email,
            "logged_in" : True,
            "logout_url" : "/logout"
        }

        return template_values

    def get(self):
        template_values = {}
        oauth_util = OAuthUtil()
	token = self.request.get('oauth_token')

        if token:
            oauth_util.get_access_token(self)
	    template_values = self.authenticated_response(oauth_util)
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_key",oauth_util.access_token.key,max_age=2629743))
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_secret",oauth_util.access_token.secret,max_age=2629743))

	else:
            access_token_key = self.request.cookies.get('access_key')
            access_token_secret = self.request.cookies.get('access_secret')
            if access_token_key:
                oauth_util.set_access_token(access_token_key, access_token_secret)
                template_values = self.authenticated_response(oauth_util)

            else:
	        oauth_url = oauth_util.get_request_token_url(self.request.url)

                template_values = {
	            "authenticated" : False,
	            "login_url" : oauth_url
	        }

        self.response.out.write(template.render('summer_status.html', template_values))

class Application(webapp.RequestHandler):
    def authenticated_response(self, oauth_util):
	user = json.loads(oauth_util.access_resource("/api/v1/user"))
	user_email = user['email']

        students = []
        is_parent = False
        query = SummerStudent.all()
        query.filter('email = ', user_email)
        student = query.get()

        if student is not None:
            students.append(student)
        else:
            query = ParentData.all()
            query.filter('email = ', user_email)
            parent = query.get()
            if parent is not None:
                is_parent = True
                for student_key in parent.students:
                    students.append(SummerStudent.get(student_key))

        if len(students) > 0:
            applied = True
            student_email = self.request.get('student_email')
            query = SummerStudent.all()
            query.filter('email = ', student_email)
            student = query.get()
            if student is None:
                logging.error("Student <%s> not expected to be NULL in datastore, but it is" % student_email)
                student = students[0]

            query = ParentData.all()
            query.filter('email = ', student.parent_email)
            parent = query.get()
            assert(parent != None)

            student_js = json.dumps(student.to_dict())
            parent_js = json.dumps(parent.to_dict())
        else:
            applied = False
            student = None
            parent = None
            student_js = json.dumps(student)
            parent_js = json.dumps(parent)

	template_values = {
	    "authenticated" : True,
	    "applied" : applied,
            "is_parent" : is_parent,
            "is_parent_js" : json.dumps(is_parent),
            "students" : students,
            "student" : student,
            "student_js" : student_js,
            "parent" : parent,
            "parent_js" : parent_js,
            "user_email_js" : json.dumps(user_email),
            "user_email" : user_email,
            "username" : user_email,
            "logged_in" : True,
            "logout_url" : "/logout"
        }

	return template_values

    def post(self):
        self.get()

    def get(self):
	template_values = {}
	oauth_util = OAuthUtil()
	token = self.request.get('oauth_token')
	user_email = self.request.get('user_email')
        make_payment = self.request.get('make_payment')

        if make_payment:
            student_email = self.request.get('student_email')
            is_parent_str = self.request.get('is_parent')

            query = SummerStudent.all()
            query.filter('email = ', student_email)
            student = query.get()

            if student is None:
                output_str = 'Please <a href="/summer/application">apply</a> first' % student_email
                self.response.out.write(output_str)
                return

            query = ParentData.all()
            query.filter('email = ', student.parent_email)
            parent = query.get()

            if parent is None:
                logging.error("Unexpected NULL parent for student <%s> with parent <%s>" %
                               (student_email, student.parent_email))

            if is_parent_str == "True":
                is_parent = True
                payee = parent
            else:
                is_parent = False
                payee = student

            payee_phone_a = ""
            payee_phone_b = ""
            payee_phone_c = ""
            phone_parts = parent.phone.split("-")
            if phone_parts is not None:
                payee_phone_a = phone_parts[0]
                payee_phone_b = phone_parts[1]
                payee_phone_c = phone_parts[2]

            template_values = {
                "authenticated" : True,
                "make_payment" : True,
                "is_parent" : is_parent,
                "is_parent_js" : json.dumps(is_parent),
                "student" : student,
                "student_js" : json.dumps(student.to_dict()),
                "payee" : payee,
                "payee_phone_a" : payee_phone_a,
                "payee_phone_b" : payee_phone_b,
                "payee_phone_c" : payee_phone_c,
                "user_email" : user_email,
                "username" : user_email,
                "logged_in" : True,
                "logout_url" : "/logout"
            }

        elif user_email:
            first_name = self.request.get('first_name')
            student_email = self.request.get('student_email')

            query = SummerStudent.all()
            query.filter('email = ', student_email)
            student = query.get()
            if student is None:
                student = SummerStudent()
                student.email = student_email
                student.applier_email = user_email

            student.first_name = first_name
            student.last_name = self.request.get('last_name')

            student.date_of_birth = self.request.get('date_of_birth')

            if self.request.get('gender') == "Female":
                student.is_female = True
            else:
                student.is_female = False

            student.grade = self.request.get('grade')
            student.school = self.request.get('school')

            student.address_1 = self.request.get('address_1')
            student.address_2 = self.request.get('address_2')
            student.city = self.request.get('city')
            student.state = self.request.get('state')
            student.zipcode = self.request.get('zip')
            student.country = self.request.get('country')

            student.session_1 = self.request.get('session_1')
            student.session_2 = self.request.get('session_2')
            student.session_3 = self.request.get('session_3')

            session_choices = { "0":[], "1":[], "2":[], "3":[] }
            session_choices[student.session_1].append("Session 1")
            session_choices[student.session_2].append("Session 2")
            session_choices[student.session_3].append("Session 3")

            student.no_choice = session_choices["0"]
            student.first_choice = session_choices["1"]
            student.second_choice = session_choices["2"]
            student.third_choice = session_choices["3"]

            student.answer_why = self.request.get('answer_why')
            student.answer_how = self.request.get('answer_how')

            student.processing_fee = self.request.get('fee')
            student.processing_fee_paid = False

            student.tuition = 'TBD'
            student.tuition_paid = False

            student.application_year = '2012'
            student.application_status = 'Processing'

            if user_email == student_email:
                is_parent = False
                student.self_applied = True
            else:
                is_parent = True
                student.self_applied = False

            student.parent_relation = self.request.get('relation')
            student.parent_email = self.request.get('parent_email')

            student.put()

            query = ParentData.all()
            query.filter('email = ', student.parent_email)
            parent = query.get()
            if parent is None:
                parent = ParentData()
                parent.email = student.parent_email

            parent.first_name = self.request.get('parent_first_name')
            parent.last_name = self.request.get('parent_last_name')
            parent.address_1 = self.request.get('parent_address_1')
            parent.address_2 = self.request.get('parent_address_2')
            parent.city = self.request.get('parent_city')
            parent.state = self.request.get('parent_state')
            parent.zipcode = self.request.get('parent_zip')
            parent.country = self.request.get('parent_country')
            parent.phone = self.request.get('parent_phone')
            parent.comments = self.request.get('parent_comments')

            if student.key() not in parent.students:
                parent.students.append(student.key())

            parent.put()

            if is_parent:
                payee = parent
            else:
                payee = student

            payee_phone_a = ""
            payee_phone_b = ""
            payee_phone_c = ""
            phone_parts = parent.phone.split("-")
            if phone_parts is not None:
                payee_phone_a = phone_parts[0]
                payee_phone_b = phone_parts[1]
                payee_phone_c = phone_parts[2]

            template_values = {
                "authenticated" : True,
                "make_payment" : True,
                "is_parent" : is_parent,
                "is_parent_js" : json.dumps(is_parent),
                "student" : student,
                "student_js" : json.dumps(student.to_dict()),
                "parent" : parent,
                "parent_js" : json.dumps(parent.to_dict()),
                "payee" : payee,
                "payee_phone_a" : payee_phone_a,
                "payee_phone_b" : payee_phone_b,
                "payee_phone_c" : payee_phone_c,
                "user_email" : user_email,
                "username" : user_email,
                "logged_in" : True,
                "logout_url" : "/logout"
            }

        elif token:
            oauth_util.get_access_token(self)
	    template_values = self.authenticated_response(oauth_util)
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_key",oauth_util.access_token.key,max_age=2629743))
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_secret",oauth_util.access_token.secret,max_age=2629743))

	else:
            access_token_key = self.request.cookies.get('access_key')
            access_token_secret = self.request.cookies.get('access_secret')
            if access_token_key:
                oauth_util.set_access_token(access_token_key, access_token_secret)
                template_values = self.authenticated_response(oauth_util)

            else:
	        oauth_url = oauth_util.get_request_token_url(self.request.url)

                template_values = {
	            "authenticated" : False,
	            "apply" : False,
	            "login_url" : oauth_url
	        }

        self.response.out.write(template.render('summer.html', template_values))
