"""
Synthetic data generator for the AI-Powered Criminal Network Analysis System.

EVERYTHING PRODUCED HERE IS FICTIONAL. No real persons, phone numbers,
accounts or cases are represented. Numbers use reserved/invalid ranges.

The generator emits five fragmented "source systems" that mirror what a real
investigating agency would hold, and deliberately plants ONE hidden multi-hop
link that no single source reveals:

    Devendra Rathi (clean businessman, no criminal record)
        -> pays Meridian Exim Pvt Ltd (shell firm)          [transactions]
        -> Meridian's director is Farid Sheikh              [FIR / registry text]
        -> Farid Sheikh shares handset 9000000041 with a
           courier recorded as "F. Shaikh"                  [entity resolution]
        -> that handset calls burner 9000000007             [CDR]
        -> burner 9000000007 is used by Vikram Sethi        [surveillance]

Only after cross-source entity resolution does the Rathi -> Sethi path exist.
"""

import csv
import json
import os
import random
from datetime import datetime, timedelta

SEED = 26189
random.seed(SEED)

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
os.makedirs(RAW, exist_ok=True)

BASE = datetime(2026, 5, 1, 8, 0, 0)


def ts(days=0, hours=0, minutes=0):
    return (BASE + timedelta(days=days, hours=hours, minutes=minutes)).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Core cast. Phone numbers are fictional (9000000xxx block).
# --------------------------------------------------------------------------

PEOPLE = [
    # name,                phone,        role,                  district
    ("Vikram Sethi",       "9000000001", "kingpin",             "Ghatkopar"),
    ("Rajesh Kumar",       "9000000002", "lieutenant",          "Kurla"),
    ("Imran Qureshi",      "9000000003", "lieutenant",          "Dongri"),
    ("Sanjay Bhosale",     "9000000004", "enforcer",            "Kurla"),
    ("Pravin Salunke",     "9000000005", "enforcer",            "Ghatkopar"),
    ("Nadeem Ansari",      "9000000006", "courier",             "Dongri"),
    ("Vikram Sethi",       "9000000007", "kingpin-burner",      "Ghatkopar"),
    ("Ashok Gaikwad",      "9000000008", "courier",             "Kurla"),
    ("Suresh Yadav",       "9000000009", "street dealer",       "Chembur"),
    ("Manoj Tiwari",       "9000000010", "street dealer",       "Chembur"),
    ("Ravi Shankar Nair",  "9000000011", "accountant",          "Fort"),
    ("Deepak Chauhan",     "9000000012", "hawala operator",     "Zaveri Bazaar"),
    ("Farid Sheikh",       "9000000041", "shell company director", "Byculla"),
    ("Devendra Rathi",     "9000000050", "businessman",         "Worli"),
    ("Kiran Deshpande",    "9000000051", "businessman aide",    "Worli"),
    ("Salim Mirza",        "9000000013", "arms supplier",       "Nagpada"),
    ("Ganesh Pawar",       "9000000014", "driver",              "Kurla"),
    ("Altaf Khan",         "9000000015", "street dealer",       "Dongri"),
    ("Ramesh Jadhav",      "9000000016", "informant",           "Chembur"),
    ("Sunil More",         "9000000017", "enforcer",            "Ghatkopar"),
]

# Aliases / spelling variants that appear in different source systems.
# The entity-resolution stage must collapse these onto one identity.
ALIAS_FORMS = {
    "Vikram Sethi": ["Vikram Sethi", "V. Sethi", "Vikram Seth", "Sethi Vikram"],
    "Rajesh Kumar": ["Rajesh Kumar", "R. Kumar", "Rajesh Kr.", "Rajesh Kumar Singh"],
    "Imran Qureshi": ["Imran Qureshi", "Imran Quraishi", "I. Qureshi"],
    "Farid Sheikh": ["Farid Sheikh", "F. Shaikh", "Fareed Shaikh"],
    "Devendra Rathi": ["Devendra Rathi", "D. Rathi", "Devendra Kumar Rathi"],
    "Nadeem Ansari": ["Nadeem Ansari", "N. Ansari"],
    "Sanjay Bhosale": ["Sanjay Bhosale", "Sanjay Bhosle"],
    "Deepak Chauhan": ["Deepak Chauhan", "D. Chauhan"],
}


def alias(name, i=None):
    forms = ALIAS_FORMS.get(name, [name])
    return forms[i % len(forms)] if i is not None else random.choice(forms)


ORGS = [
    "Meridian Exim Pvt Ltd",
    "Rathi Infrastructure Ltd",
    "Sunrise Logistics",
    "Al-Noor Trading Co",
    "Konkan Marine Services",
]

VEHICLES = [
    "MH01AB1234", "MH02CD5678", "MH03EF9012", "MH12GH3456", "MH04JK7788",
]

LOCATIONS = {
    "Ghatkopar":     (19.0860, 72.9080),
    "Kurla":         (19.0726, 72.8790),
    "Dongri":        (18.9600, 72.8370),
    "Chembur":       (19.0620, 72.8990),
    "Fort":          (18.9350, 72.8350),
    "Zaveri Bazaar": (18.9520, 72.8320),
    "Byculla":       (18.9760, 72.8330),
    "Worli":         (19.0170, 72.8170),
    "Nagpada":       (18.9660, 72.8290),
    "JNPT Port":     (18.9490, 72.9510),
    "Bhiwandi Godown": (19.2960, 73.0630),
}

PHONE_OF = {}
for nm, ph, role, dist in PEOPLE:
    PHONE_OF.setdefault(nm, []).append(ph)


# --------------------------------------------------------------------------
# Devanagari name forms. A real corpus is not "English with some Hindi words" -
# a large share of FIRs and station diaries are written wholly in Devanagari,
# and the same person is spelled one way there and another in an English CDR
# export. These forms exercise the transliteration and phonetic-key matching in
# pipeline/translit.py.
# --------------------------------------------------------------------------

DEVANAGARI = {
    "Vikram Sethi": "विक्रम सेठी", "Rajesh Kumar": "राजेश कुमार",
    "Imran Qureshi": "इमरान कुरैशी", "Sanjay Bhosale": "संजय भोसले",
    "Nadeem Ansari": "नदीम अंसारी", "Ashok Gaikwad": "अशोक गायकवाड",
    "Suresh Yadav": "सुरेश यादव", "Manoj Tiwari": "मनोज तिवारी",
    "Farid Sheikh": "फरीद शेख", "Devendra Rathi": "देवेंद्र राठी",
    "Salim Mirza": "सलीम मिर्ज़ा", "Ganesh Pawar": "गणेश पवार",
    "Altaf Khan": "अल्ताफ खान", "Deepak Chauhan": "दीपक चौहान",
    "Kiran Deshpande": "किरण देशपांडे", "Ravi Shankar Nair": "रवि शंकर नायर",
    "Pravin Salunke": "प्रवीण सालुंके", "Sunil More": "सुनील मोरे",
}

DEVANAGARI_LOC = {
    "Ghatkopar": "घाटकोपर", "Kurla": "कुर्ला", "Dongri": "डोंगरी",
    "Chembur": "चेंबूर", "Byculla": "भायखला", "Worli": "वरली",
    "Nagpada": "नागपाडा", "Bhiwandi Godown": "भिवंडी गोदाम",
    "Zaveri Bazaar": "झवेरी बाजार", "JNPT Port": "जेएनपीटी बंदरगाह",
    "Fort": "फोर्ट",
}

ASCII_TO_DEVA_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


# --------------------------------------------------------------------------
# 1. FIRs - unstructured narrative text (English and Devanagari)
# --------------------------------------------------------------------------

FIR_TEMPLATES = [
    ("FIR/2026/{n:04d}", "Ghatkopar Police Station", "NDPS Act s.21, s.29",
     "On {date} at approximately {time} hrs, acting on secret information, a raiding party "
     "intercepted vehicle {vehicle} near {loc}. Two persons, namely {p1} (mob. {ph1}) and "
     "{p2}, were apprehended. Recovered 1.2 kg of contraband suspected to be MD powder. "
     "During interrogation {p1} disclosed that the consignment was arranged on instructions "
     "of one {p3}, who is stated to be operating from {loc2}. Aaropi ne bataya ki maal "
     "Bhiwandi godown se aaya tha. Further investigation is in progress."),

    ("FIR/2026/{n:04d}", "Kurla Police Station", "IPC s.384, s.506",
     "Complainant states that on {date} he was threatened at his shop near {loc} by "
     "{p1} and one associate for non-payment of protection money. The accused came in a "
     "white Innova bearing registration {vehicle}. Complainant identified {p1} from "
     "photographs. {p1} is reportedly working under {p3}. Mobile number {ph1} was used to "
     "make threatening calls to the complainant."),

    ("FIR/2026/{n:04d}", "Byculla Police Station", "IPC s.420, s.120B, PMLA",
     "The complaint received from the Registrar of Companies alleges that "
     "{org} is a paper entity with no genuine business activity. The sole director of "
     "{org} is recorded as {p1}, resident of {loc}. Funds amounting to several crore "
     "were routed through the said entity on behalf of {org2}. Notice has been issued to "
     "{p1} at mobile {ph1}. Documents seized indicate involvement of {p3} in arranging "
     "the layering of funds."),

    ("FIR/2026/{n:04d}", "Dongri Police Station", "Arms Act s.25",
     "During a routine naka bandi at {loc} on {date}, one person disclosing his name as "
     "{p1} was found in possession of one country-made pistol. He was travelling on a "
     "motorcycle {vehicle}. On questioning he stated the weapon was handed over to him by "
     "{p3} at {loc2}. Uska kehna hai ki usse sirf pahunchane ko kaha gaya tha. Accused "
     "was using mobile {ph1}."),

    ("FIR/2026/{n:04d}", "Chembur Police Station", "NDPS Act s.27",
     "Two persons {p1} and {p2} were caught red-handed selling small quantity contraband "
     "near {loc}. Both admitted to procuring the material from {p3}. Cash of Rs 42,000 was "
     "recovered. Contact number of accused {p1} is {ph1}."),
]


DEVANAGARI_FIR_TEMPLATES = [
    ("घाटकोपर पुलिस स्टेशन", "एनडीपीएस अधिनियम धारा 21, 29",
     "दिनांक {date} को समय {time} बजे गुप्त सूचना के आधार पर छापेमारी दल द्वारा "
     "{loc} के पास वाहन क्रमांक {vehicle} को रोका गया। वाहन में सवार आरोपी "
     "{p1} (मो. {ph1}) तथा {p2} को हिरासत में लिया गया। तलाशी के दौरान 900 ग्राम "
     "प्रतिबंधित पदार्थ बरामद हुआ। पूछताछ के दौरान {p1} ने बताया कि यह खेप "
     "{p3} के कहने पर मंगाई गई थी। आगे की जांच जारी है।"),

    ("कुर्ला पुलिस स्टेशन", "भादंसं धारा 384, 506",
     "फरियादी के बयान के अनुसार दिनांक {date} को {loc} स्थित उसकी दुकान पर "
     "{p1} तथा उसके एक साथी ने हफ्ता वसूली को लेकर धमकी दी। आरोपी सफेद रंग की "
     "गाड़ी क्रमांक {vehicle} से आए थे। {p1} पूर्व में भी दर्ज अपराधों में शामिल "
     "रहा है तथा {p3} के लिए काम करता है। धमकी भरे कॉल मोबाइल नंबर {ph1} से "
     "किए गए हैं।"),

    ("डोंगरी पुलिस स्टेशन", "आयुध अधिनियम धारा 25",
     "दिनांक {date} को {loc} में नाकाबंदी के दौरान {p1} नामक व्यक्ति के कब्जे से "
     "एक देशी पिस्तौल बरामद हुई। वह मोटरसाइकिल क्रमांक {vehicle} पर सवार था। "
     "पूछताछ में उसने बताया कि उसने {p3} से माल लिया था और उसे केवल पहुंचाने "
     "को कहा गया था। आरोपी मोबाइल {ph1} का प्रयोग कर रहा था।"),
]


def gen_devanagari_firs(start_index):
    """FIRs written wholly in Devanagari, including Devanagari numerals."""
    combos = [
        ("Ashok Gaikwad", "Ganesh Pawar", "Rajesh Kumar", "Ghatkopar", 0, False),
        ("Sanjay Bhosale", "Sunil More", "Vikram Sethi", "Kurla", 1, True),
        ("Altaf Khan", "Suresh Yadav", "Imran Qureshi", "Dongri", 2, False),
    ]
    out = []
    for i, (p1, p2, p3, loc, tmpl, deva_digits) in enumerate(combos):
        station, sections, body = DEVANAGARI_FIR_TEMPLATES[tmpl]
        d = BASE + timedelta(days=2 + i * 5)
        phone = PHONE_OF[p1][0]
        text = body.format(
            date=d.strftime("%d/%m/%Y"), time=d.strftime("%H%M"),
            vehicle=VEHICLES[i % len(VEHICLES)],
            loc=DEVANAGARI_LOC[loc],
            p1=DEVANAGARI[p1], p2=DEVANAGARI[p2], p3=DEVANAGARI[p3],
            ph1=phone.translate(ASCII_TO_DEVA_DIGITS) if deva_digits else phone,
        )
        n = start_index + i
        out.append({
            "fir_id": f"FIR/2026/{n:04d}",
            "station": station, "sections": sections,
            "registered_on": d.isoformat(timespec="seconds"),
            "district": loc, "language": "hi-IN (Devanagari)",
            "narrative": text,
        })
    return out


def gen_firs():
    firs = []
    combos = [
        ("Nadeem Ansari", "Ashok Gaikwad", "Imran Qureshi", "Dongri", "Bhiwandi Godown", 0),
        ("Sanjay Bhosale", "Ganesh Pawar", "Rajesh Kumar", "Kurla", "Ghatkopar", 1),
        ("Farid Sheikh", "Kiran Deshpande", "Deepak Chauhan", "Byculla", "Fort", 2),
        ("Altaf Khan", "Nadeem Ansari", "Salim Mirza", "Nagpada", "Dongri", 3),
        ("Suresh Yadav", "Manoj Tiwari", "Rajesh Kumar", "Chembur", "Kurla", 4),
        ("Pravin Salunke", "Sunil More", "Vikram Sethi", "Ghatkopar", "Kurla", 1),
        ("Ashok Gaikwad", "Ganesh Pawar", "Imran Qureshi", "Kurla", "JNPT Port", 0),
        ("Manoj Tiwari", "Suresh Yadav", "Altaf Khan", "Chembur", "Dongri", 4),
    ]
    for i, (p1, p2, p3, loc, loc2, tmpl_idx) in enumerate(combos):
        code, station, sections, body = FIR_TEMPLATES[tmpl_idx]
        d = BASE + timedelta(days=i * 3)
        text = body.format(
            n=101 + i,
            date=d.strftime("%d/%m/%Y"),
            time=d.strftime("%H%M"),
            vehicle=VEHICLES[i % len(VEHICLES)],
            loc=loc, loc2=loc2,
            p1=alias(p1, i), p2=alias(p2, i + 1), p3=alias(p3, i + 2),
            ph1=PHONE_OF[p1][0],
            org="Meridian Exim Pvt Ltd",
            org2="Rathi Infrastructure Ltd",
        )
        firs.append({
            "fir_id": code.format(n=101 + i),
            "station": station,
            "sections": sections,
            "registered_on": d.isoformat(timespec="seconds"),
            "district": loc,
            "language": "en-IN (mixed Devanagari transliteration)",
            "narrative": text,
        })
    firs += gen_devanagari_firs(201)
    return firs


# --------------------------------------------------------------------------
# 2. Call Detail Records
# --------------------------------------------------------------------------

CALL_BACKBONE = [
    # (caller, callee, n_calls, day_start, day_span, cell)
    ("9000000001", "9000000002", 22, 0, 40, "Ghatkopar"),
    ("9000000001", "9000000003", 18, 0, 40, "Ghatkopar"),
    ("9000000002", "9000000004", 26, 0, 40, "Kurla"),
    ("9000000002", "9000000008", 19, 0, 40, "Kurla"),
    ("9000000003", "9000000006", 24, 0, 40, "Dongri"),
    ("9000000003", "9000000015", 15, 0, 40, "Dongri"),
    ("9000000004", "9000000017", 11, 0, 40, "Ghatkopar"),
    ("9000000002", "9000000009", 14, 0, 40, "Chembur"),
    ("9000000009", "9000000010", 20, 0, 40, "Chembur"),
    ("9000000006", "9000000008", 9, 0, 40, "Kurla"),
    ("9000000011", "9000000012", 17, 0, 40, "Fort"),
    ("9000000002", "9000000011", 8, 0, 40, "Fort"),
    ("9000000013", "9000000003", 7, 0, 40, "Nagpada"),
    ("9000000014", "9000000004", 12, 0, 40, "Kurla"),
    # ---- the quiet bridge: shell director's handset -> kingpin burner ----
    ("9000000041", "9000000007", 4, 12, 16, "Byculla"),
    ("9000000041", "9000000012", 9, 5, 30, "Zaveri Bazaar"),
    ("9000000050", "9000000051", 31, 0, 40, "Worli"),
    ("9000000051", "9000000041", 6, 8, 20, "Worli"),
]


def gen_cdr():
    rows = []
    imei_pool = {ph: f"3550{random.randint(10**10, 10**11 - 1)}" for _, ph, _, _ in PEOPLE}
    # burner shares an IMEI with the kingpin's primary handset - a classic tell
    imei_pool["9000000007"] = imei_pool["9000000001"]

    for a, b, n, d0, span, cell in CALL_BACKBONE:
        for _ in range(n):
            day = d0 + random.random() * span
            t = BASE + timedelta(days=day, minutes=random.randint(0, 1439))
            rows.append({
                "call_id": f"C{len(rows)+1:06d}",
                "caller": a, "callee": b,
                "start_time": t.isoformat(timespec="seconds"),
                "duration_sec": random.choice([12, 25, 47, 63, 88, 121, 190, 240]),
                "call_type": random.choice(["VOICE", "VOICE", "VOICE", "SMS"]),
                "cell_tower": cell,
                "lat": LOCATIONS[cell][0] + random.uniform(-0.004, 0.004),
                "lon": LOCATIONS[cell][1] + random.uniform(-0.004, 0.004),
                "imei": imei_pool.get(a, ""),
            })

    # burst anomaly: heavy chatter in the 48h before the Bhiwandi seizure (day 21)
    for _ in range(38):
        a, b = random.choice([("9000000002", "9000000008"), ("9000000003", "9000000006"),
                              ("9000000001", "9000000002"), ("9000000006", "9000000008")])
        t = BASE + timedelta(days=21 + random.random() * 2, minutes=random.randint(0, 1439))
        rows.append({
            "call_id": f"C{len(rows)+1:06d}", "caller": a, "callee": b,
            "start_time": t.isoformat(timespec="seconds"),
            "duration_sec": random.choice([8, 14, 22, 31]),
            "call_type": "VOICE", "cell_tower": "Bhiwandi Godown",
            "lat": LOCATIONS["Bhiwandi Godown"][0] + random.uniform(-0.006, 0.006),
            "lon": LOCATIONS["Bhiwandi Godown"][1] + random.uniform(-0.006, 0.006),
            "imei": imei_pool.get(a, ""),
        })

    # Tradecraft: two subjects who NEVER call each other but whose handsets keep
    # appearing on the same cell site within minutes. The kingpin's burner and
    # the hawala operator meet in person and settle by cash; nothing in the call
    # graph connects them, and only co-presence exposes the relationship.
    for k in range(6):
        cell = ["Zaveri Bazaar", "Byculla"][k % 2]
        base_day = 7 + k * 4
        anchor = BASE + timedelta(days=base_day, hours=11, minutes=random.randint(0, 90))
        for me, other, offset in (("9000000007", "9000000002", 0),
                                  ("9000000012", "9000000011", random.randint(6, 40))):
            t = anchor + timedelta(minutes=offset)
            rows.append({
                "call_id": f"C{len(rows)+1:06d}", "caller": me, "callee": other,
                "start_time": t.isoformat(timespec="seconds"),
                "duration_sec": random.choice([31, 44, 58, 77]),
                "call_type": "VOICE", "cell_tower": cell,
                "lat": LOCATIONS[cell][0] + random.uniform(-0.002, 0.002),
                "lon": LOCATIONS[cell][1] + random.uniform(-0.002, 0.002),
                "imei": imei_pool.get(me, ""),
            })

    # background noise: unrelated subscribers, so the graph is not trivially clean
    noise_numbers = [f"9000000{n:03d}" for n in range(200, 260)]
    for _ in range(220):
        a, b = random.sample(noise_numbers, 2)
        cell = random.choice(list(LOCATIONS))
        t = BASE + timedelta(days=random.random() * 40, minutes=random.randint(0, 1439))
        rows.append({
            "call_id": f"C{len(rows)+1:06d}", "caller": a, "callee": b,
            "start_time": t.isoformat(timespec="seconds"),
            "duration_sec": random.randint(10, 300), "call_type": "VOICE",
            "cell_tower": cell,
            "lat": LOCATIONS[cell][0] + random.uniform(-0.01, 0.01),
            "lon": LOCATIONS[cell][1] + random.uniform(-0.01, 0.01),
            "imei": "",
        })
    rows.sort(key=lambda r: r["start_time"])
    return rows


# --------------------------------------------------------------------------
# 3. Financial transactions
# --------------------------------------------------------------------------

ACCOUNTS = {
    "Devendra Rathi": "ACRTH0001",
    "Rathi Infrastructure Ltd": "ACRTH0002",
    "Meridian Exim Pvt Ltd": "ACMER0011",
    "Farid Sheikh": "ACFSH0012",
    "Deepak Chauhan": "ACDCH0021",
    "Ravi Shankar Nair": "ACRSN0022",
    "Sunrise Logistics": "ACSUN0031",
    "Al-Noor Trading Co": "ACALN0032",
    "Rajesh Kumar": "ACRKR0041",
    "Vikram Sethi": "ACVSE0051",
    "Konkan Marine Services": "ACKON0061",
    # ordinary vendors, used only for benign background noise
    "Sai Traders": "ACSAI0071",
    "Nova Stationers": "ACNOV0072",
    "Gokhale Contractors": "ACGOK0073",
    "Prime Facility Services": "ACPRI0074",
}

BENIGN = ["Sai Traders", "Nova Stationers", "Gokhale Contractors",
          "Prime Facility Services"]


def gen_transactions():
    rows = []
    def add(src, dst, amt, day, mode, note):
        t = BASE + timedelta(days=day, minutes=random.randint(0, 900))
        rows.append({
            "txn_id": f"T{len(rows)+1:06d}",
            "from_account": ACCOUNTS[src], "from_name": src,
            "to_account": ACCOUNTS[dst], "to_name": dst,
            "amount_inr": amt, "mode": mode,
            "timestamp": t.isoformat(timespec="seconds"), "narration": note,
        })

    # layering chain from the legitimate-looking business into the network
    for k in range(6):
        add("Rathi Infrastructure Ltd", "Meridian Exim Pvt Ltd",
            random.choice([1850000, 2400000, 1975000, 3100000]), 4 + k * 4,
            "RTGS", "consultancy charges")
    for k in range(6):
        add("Meridian Exim Pvt Ltd", "Sunrise Logistics",
            random.choice([1420000, 1680000, 2210000]), 6 + k * 4, "NEFT", "freight advance")
    for k in range(5):
        add("Sunrise Logistics", "Al-Noor Trading Co",
            random.choice([1310000, 1590000, 1875000]), 8 + k * 4, "NEFT", "purchase order")
    # circular / round-tripping flow - classic laundering signature
    for k in range(4):
        add("Al-Noor Trading Co", "Konkan Marine Services", 1250000, 10 + k * 5, "NEFT", "charter hire")
        add("Konkan Marine Services", "Rathi Infrastructure Ltd", 1180000, 12 + k * 5, "NEFT", "refund")
    # cash-out legs toward the network
    for k in range(7):
        add("Al-Noor Trading Co", "Deepak Chauhan",
            random.choice([480000, 620000, 750000]), 9 + k * 3, "CASH", "settlement")
    for k in range(6):
        add("Deepak Chauhan", "Ravi Shankar Nair",
            random.choice([390000, 510000, 460000]), 11 + k * 3, "CASH", "hawala settlement")
    for k in range(5):
        add("Ravi Shankar Nair", "Rajesh Kumar",
            random.choice([250000, 310000, 280000]), 13 + k * 3, "CASH", "-")
    add("Farid Sheikh", "Meridian Exim Pvt Ltd", 100000, 1, "NEFT", "director capital infusion")
    add("Rajesh Kumar", "Vikram Sethi", 900000, 30, "CASH", "-")

    # structuring: many sub-threshold deposits just under the reporting limit
    for k in range(14):
        add("Deepak Chauhan", "Meridian Exim Pvt Ltd", random.choice([49000, 48500, 49500]),
            18 + k * 0.4, "CASH", "cash deposit")

    # Benign background transactions. Deliberately confined to ordinary vendor
    # accounts (plus routine payables from the two real businesses) so the noise
    # never manufactures a false shortcut between distant parts of the network -
    # the Rathi -> Sethi link must remain discoverable only through the layering
    # chain, exactly as it would be in a real case.
    for _ in range(50):
        src, dst = random.sample(BENIGN, 2)
        add(src, dst, random.randint(5000, 90000),
            random.random() * 40, random.choice(["UPI", "NEFT", "IMPS"]), "vendor payment")
    for _ in range(14):
        add("Rathi Infrastructure Ltd", random.choice(BENIGN), random.randint(20000, 180000),
            random.random() * 40, random.choice(["NEFT", "UPI"]), "site expenses")
    for _ in range(8):
        add("Meridian Exim Pvt Ltd", random.choice(BENIGN), random.randint(8000, 60000),
            random.random() * 40, "UPI", "office expenses")

    rows.sort(key=lambda r: r["timestamp"])
    return rows


# --------------------------------------------------------------------------
# 4. Criminal history records
# --------------------------------------------------------------------------

def gen_criminal_records():
    hist = [
        ("Vikram Sethi", "9000000001", 4, "NDPS, Extortion, Arms Act", "2019-2024"),
        ("Rajesh Kumar", "9000000002", 3, "NDPS, Extortion", "2020-2025"),
        ("Imran Qureshi", "9000000003", 3, "NDPS, Smuggling", "2018-2024"),
        ("Sanjay Bhosale", "9000000004", 2, "Assault, Extortion", "2021-2025"),
        ("Nadeem Ansari", "9000000006", 2, "NDPS", "2022-2025"),
        ("Salim Mirza", "9000000013", 3, "Arms Act", "2017-2023"),
        ("Suresh Yadav", "9000000009", 1, "NDPS s.27", "2024"),
        ("Altaf Khan", "9000000015", 1, "NDPS s.27", "2023"),
        ("Deepak Chauhan", "9000000012", 1, "FEMA violation", "2021"),
        ("Farid Sheikh", "9000000041", 0, "-", "-"),
        ("Kiran Deshpande", "9000000051", 0, "-", "-"),
        ("Devendra Rathi", "9000000050", 0, "-", "-"),
    ]
    return [{
        "record_id": f"CR{i+1:04d}",
        "name": alias(n, i), "phone": p, "prior_cases": c,
        "offence_categories": o, "active_period": yr,
        "police_district": next((d for nm, ph, r, d in PEOPLE if nm == n), "-"),
    } for i, (n, p, c, o, yr) in enumerate(hist)]


# --------------------------------------------------------------------------
# 5. Surveillance reports + 6. Social media intelligence (unstructured)
# --------------------------------------------------------------------------

def gen_surveillance():
    obs = [
        ("SUR/2026/01", 14, "Bhiwandi Godown",
         "Static observation from 2100 to 0230 hrs. Subject {a} arrived in vehicle {v1} "
         "accompanied by two unidentified males. At 2340 hrs a second vehicle {v2} arrived; "
         "occupant identified as {b}. Consignment transferred between vehicles. Subject {a} "
         "was observed using a second handset, number established as {burner}, distinct from "
         "his known number 9000000001."),
        ("SUR/2026/02", 17, "Zaveri Bazaar",
         "Subject {c} observed entering premises of a jewellery establishment at 1215 hrs "
         "carrying a cloth bag. Exited at 1305 hrs without the bag. Met one {d} outside, "
         "conversation lasted 6 minutes. {d} departed on motorcycle {v1}."),
        ("SUR/2026/03", 22, "Worli",
         "Subject {e}, a builder of standing with no adverse record, was observed at a "
         "private club. He was joined by his associate {k} for approximately 40 minutes. "
         "No exchange observed. Nothing adverse noticed against the subject."),
        ("SUR/2026/04", 26, "JNPT Port",
         "Container movement observed. {b} present at gate no. 4 along with driver {g} "
         "operating vehicle {v2}. Documentation reportedly in the name of Konkan Marine "
         "Services."),
        ("SUR/2026/05", 30, "Kurla",
         "Meeting observed at a roadside eatery between {h} and {j}. Both arrived separately. "
         "{h} handed over an envelope. Subjects dispersed after 12 minutes."),
    ]
    out = []
    for i, (rid, day, loc, body) in enumerate(obs):
        text = body.format(
            a=alias("Vikram Sethi", i), b=alias("Imran Qureshi", i),
            c=alias("Deepak Chauhan", i), d=alias("Nadeem Ansari", i),
            e=alias("Devendra Rathi", i + 1), f=alias("Farid Sheikh", i),
            k="Kiran Deshpande",
            g="Ganesh Pawar", h=alias("Rajesh Kumar", i),
            j=alias("Ravi Shankar Nair", i),
            v1=VEHICLES[i % len(VEHICLES)], v2=VEHICLES[(i + 2) % len(VEHICLES)],
            burner="9000000007",
        )
        out.append({
            "report_id": rid, "observed_on": ts(days=day), "location": loc,
            "lat": LOCATIONS[loc][0], "lon": LOCATIONS[loc][1],
            "unit": "Anti Narcotics Cell", "observation": text,
        })
    return out


def gen_social():
    posts = [
        ("@mumbai_watch_07", 9, "Chembur",
         "Bhai log ka naya thikana Chembur station ke peeche hai. {a} aur uska aadmi roz "
         "raat ko dikhte hain. Contact 9000000009 pe chalta hai sab."),
        ("@bharat_crime_feed", 16, "Kurla",
         "Sources say {b} controls collection in Kurla belt now after the old setup broke. "
         "Seen frequently with {c}."),
        ("@localeye_mum", 23, "Byculla",
         "Meridian Exim ka office band pada hai lekin crore ka transaction ho raha hai. "
         "Director {d} ka naam kagaz pe hai bas."),
        ("@portside_news", 28, "JNPT Port",
         "Container clearance racket at JNPT. Names doing rounds: {e}, and a logistics firm "
         "linked to Konkan Marine Services."),
        ("@mumbai_watch_07", 33, "Ghatkopar",
         "{f} back in Ghatkopar. Two vehicles {v} always parked outside his building."),
    ]
    out = []
    for i, (handle, day, loc, body) in enumerate(posts):
        out.append({
            "post_id": f"SM{i+1:04d}", "platform": "X", "handle": handle,
            "posted_on": ts(days=day), "geo_hint": loc,
            "text": body.format(
                a=alias("Suresh Yadav", i), b=alias("Rajesh Kumar", i),
                c=alias("Sanjay Bhosale", i), d=alias("Farid Sheikh", i + 1),
                e=alias("Imran Qureshi", i), f=alias("Vikram Sethi", i + 1),
                v=VEHICLES[i % len(VEHICLES)]),
            "reliability": random.choice(["low", "low", "medium"]),
        })
    return out


# --------------------------------------------------------------------------

def write_json(name, obj):
    p = os.path.join(RAW, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    return p, len(obj)


def write_csv(name, rows):
    p = os.path.join(RAW, name)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return p, len(rows)


def gen_scanned_firs():
    """
    FIRs that exist ONLY as paper. They are rendered to page images by
    make_scans.py and reach the pipeline through OCR, never as clean text - so
    the intelligence they carry is genuinely unavailable without the OCR path.
    The ground truth is kept alongside purely to measure OCR accuracy.
    """
    recs = [
        ("FIR/2026/0301", "Worli Police Station", "IPC s.420, s.120B", 19, "Worli",
         "The complainant, a director of a construction firm, states that on "
         "18/05/2026 he was approached at his office in Worli by one Kiran Deshpande "
         "who offered to arrange clearances through intermediaries. Kiran Deshpande "
         "stated that payments would be routed through Meridian Exim Pvt Ltd. "
         "The complainant was given a contact number 9000000041 and asked to speak "
         "to one F. Shaikh. No payment has been made as on date."),
        ("FIR/2026/0302", "Chembur Police Station", "NDPS Act s.27", 24, "Chembur",
         "On 23/05/2026 during patrolling near Chembur, two persons namely Suresh "
         "Yadav (mob. 9000000009) and Manoj Tiwari were found in suspicious "
         "circumstances. On search, small quantity contraband was recovered. Both "
         "stated that supply is arranged by Rajesh Kumar of Kurla. Suresh Yadav is "
         "reportedly working under Rajesh Kumar."),
    ]
    return [{
        "fir_id": fid, "station": st, "sections": sec,
        "registered_on": ts(days=day), "district": dist,
        "language": "en-IN", "medium": "paper - scanned",
        "narrative": body,
    } for fid, st, sec, day, dist, body in recs]


def main():
    outputs = [
        write_json("scanned_source.json", gen_scanned_firs()),
        write_json("firs.json", gen_firs()),
        write_csv("cdr.csv", gen_cdr()),
        write_csv("transactions.csv", gen_transactions()),
        write_csv("criminal_records.csv", gen_criminal_records()),
        write_json("surveillance.json", gen_surveillance()),
        write_json("social_media.json", gen_social()),
    ]
    print("Synthetic source data written (all records fictional):")
    for path, n in outputs:
        print(f"  {os.path.basename(path):<24} {n:>5} records")


if __name__ == "__main__":
    main()
