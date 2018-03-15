#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os, json, sys, re, time
from datetime import date, timedelta, datetime
from pymongo import MongoClient
from bson.code import Code
from export import export_csv
from flask import Flask, render_template, request, make_response
from flask_caching import Cache
from flask_compress import Compress

try:
    with open(os.path.join(os.path.dirname(__file__), '..', '..', 'config.json')) as confile:
         conf = json.loads(confile.read())
except Exception as e:
    sys.stderr.write("ERROR: Impossible to read config.json: %s %s" % (type(e), e))
    exit(1)
selected_field =  conf.get('export', {}).get('selected_field', None)
extra_fields =  conf.get('export', {}).get('extra_fields', [])

try:
    mongodb = MongoClient(conf['mongo']['host'], conf['mongo']['port'])[conf['mongo']['db']]['tweets']
except Exception as e:
    sys.stderr.write("ERROR: Could not initiate connection to MongoDB: %s %s" % (type(e), e))
    exit(1)

app = Flask(__name__)
Compress(app)
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

def init_args():
    return {
      'startdate': (date.today() - timedelta(days=7)).isoformat(),
      'enddate': date.today().isoformat(),
      'query': '',
      'filters': '',
      'selected_option': selected_field is not None,
      'selected': "checked" if selected_field else None
    }

@app.route("/")
@cache.cached(timeout=3600)
def home():
    return render_template("home.html", **init_args())

@app.route("/api/histo")
def timeline():

    # TODO parse arg level + query
    level = "day"

    buildDate = "date.getFullYear()"
    if level in "month day hour minute":
        buildDate += "+'-'+(date.getMonth()+1)"
    if level in "day hour minute":
        buildDate += "+'-'+date.getDate()"
    if level in "hour minute":
        buildDate += "+' '+date.getHours()+'H'"
    if level in "minute":
        buildDate += "+date.getMinutes()"
    dateKey = Code("""
      function(doc){
        var date = new Date(doc.created_at);
        var dateKey = """+buildDate+""";
        return {dat: dateKey};
      }
    """)

    simpleSum = Code("""
      function(doc, prev){
        prev.ct++;
      }
    """)

    # condition = parsedQuery
    stats = mongodb.group(key=dateKey, condition={}, initial={"ct": 0}, reduce=simpleSum)
    return make_response("time,count\n" + "\n".join(["%s,%s" % (el["dat"], el["ct"]) for el in stats]))


@app.route("/download")
def download():
    args = init_args()
    errors = []
    for arg in ['startdate', 'enddate', 'query', 'filters']:
        args[arg] = request.args.get(arg)
        if args[arg] is None:
            errors.append('Field "%s" missing' % arg)
            args[arg] = ''
        if arg.endswith('date'):
            try:
                d = datetime.strptime(args[arg], "%Y-%m-%d")
                if arg == "enddate":
                    d += timedelta(days=1)
                args[arg.replace("date", "time")] = time.mktime(d.timetuple())
            except Exception as e:
                errors.append(u'Field "%s": « %s » is not a valid date (%s: %s)' % (arg, args[arg], type(e), e))
    if selected_field:
        args['selected'] = request.args.get('selected')
    if args.get("starttime", 0) >= args.get("endtime", 0):
        errors.append('Field "startdate" should be older than field "enddate"')
    if errors:
        return make_response("\n".join(["error"] + errors))
    return queryData(args)

@cache.memoize(1800)
def queryData(args):
    query = {
      "$and": [
        {"timestamp": {"$gte": args["starttime"]}},
        {"timestamp": {"$lt": args["endtime"]}}
      ]
    }
    if args["query"]:
        for q in args["query"].split('|'):
            query["$and"].append({
              "text": re.compile(r"%s" % q, re.I)
            })
    if args["filters"]:
        for q in args["filters"].split('|'):
            query["$and"].append({
              "text": {"$not": re.compile(r"%s" % q, re.I)}
            })
    if selected_field and args['selected'] == 'checked':
        query["$and"].append({selected_field: True})
    mongoiterator = mongodb.find(query, sort=[("_id", 1)])
    csv = export_csv(mongoiterator, extra_fields)
    res = make_response(csv)
    res.headers["Content-Type"] = "text/csv; charset=UTF-8"
    res.headers["Content-Disposition"] = "attachment; filename=tweets-%s-%s-%s.csv" % (args['startdate'], args['enddate'], args['query'])
    return res

if __name__ == "__main__":
    app.run(debug=True)
