# Biblio Explorer — Ενοποίηση & Οπτικοποίηση Βιβλιογραφικών Δεδομένων

Το **Biblio Explorer** είναι ένα έργο ενοποίησης, μοντελοποίησης, επεξεργασίας και οπτικοποίησης ακαδημαϊκών βιβλιογραφικών δεδομένων. Το σύστημα συνδυάζει δεδομένα από DBLP, iCORE26 και Kaggle, τα μετασχηματίζει σε ενιαίο σχήμα βάσης δεδομένων και παρέχει ένα διαδραστικό dashboard με Flask και D3.js.

**Ομάδα**: Θεοφάνης Τόμπολης AM 4855, Αθανάσιος Φυτιλής AM 5381, Μαρίνος Αριστείδου AM 5397  
**Μάθημα**: MYE030 / PLE045 — Προχωρημένα Θέματα Τεχνολογίας Λογισμικού

---

## 🎯 Επισκόπηση έργου

### Φάση I: ETL & αρχιτεκτονική βάσης δεδομένων

- **Βάση δεδομένων**: MariaDB 10.4+ / MySQL 8.0, βάση `biblio_db`, θύρα `3307`.
- **Πηγές δεδομένων**:
  - Μορφοποιημένα δεδομένα DBLP για `inproceedings` και `articles` σε CSV.
  - Κατατάξεις συνεδρίων από το iCORE26 (`conference_rankings`).
  - Κατατάξεις περιοδικών από Kaggle (`journal_ranking_data_raw`) σε TSV.
- **Σχήμα δεδομένων**: Ενιαίο σχήμα `papers` για συνέδρια και περιοδικά, ώστε να υποστηρίζονται αποδοτικά ερωτήματα και αναλύσεις ανά συγγραφέα.
- **Επεξεργασία δεδομένων**:
  - Διόρθωση προβλημάτων κωδικοποίησης χαρακτήρων και μη έγκυρων διαχωριστικών.
  - Ανάλυση λιστών πολλαπλών συγγραφέων και δημιουργία σχέσεων N:M με σειρά εμφάνισης.
  - Εξαγωγή περίπου 1,4 εκατομμυρίων μοναδικών αντικειμένων συγγραφέων από ακατέργαστες συμβολοσειρές.
- **Αντιστοίχιση venues**:
  - Χρήση exact matches και κανονικοποιήσεων με Regex για αντιστοίχιση αναφορών DBLP με τις κατατάξεις iCORE/Kaggle.

### Φάση II: Πρωτότυπο backend εφαρμογής

- **Τεχνολογίες**: Python 3.10+, Flask, `mysql-connector-python`.
- **Αρχιτεκτονική**: REST API endpoints με Flask Blueprints ανά βασική οντότητα.
- **Βελτιώσεις απόδοσης**:
  - MySQL Connection Pool για ταυτόχρονα αιτήματα.
  - Βαριές αναλυτικές συναθροίσεις στη βάση με βελτιστοποιημένα SQL Views.

### Φάση III: Πλήρης διαδραστική εφαρμογή

- **Frontend**: Σύγχρονο responsive dark-mode περιβάλλον με HTML/CSS, CSS variables, active states και custom UI components.
- **Αναζήτηση & φίλτρα**: Live autocomplete για συνέδρια και περιοδικά, καθώς και φίλτρα εύρους ετών που επηρεάζουν δυναμικά πίνακες και γραφήματα.
- **Οπτικοποιήσεις με D3.js**:
  - Επαναχρησιμοποιήσιμα, animated line charts με hover tooltips για papers και authors ανά έτος.
  - Multi-select comparison charts για σύγκριση πολλών συνεδρίων ή περιοδικών στην ίδια χρονοσειρά.
- **Σελίδες προφίλ**:
  - Προφίλ συνεδρίων, περιοδικών, συγγραφέων και ετών.
  - Εμφάνιση rankings, H-index, SJR, quartile, ενεργών ετών και πλήθους δημοσιεύσεων/συγγραφέων.
  - Scrollable πίνακες δημοσιεύσεων με συνδέσμους προς εξωτερικές πηγές DBLP και EE.

### Κατάσταση Φάσης III — 2026-05-13

- Τα βασικά frontend/backend deliverables για αναζήτηση, προφίλ και γραφήματα έχουν ολοκληρωθεί.
- Έχει προστεθεί security hardening με API rate limiting (`100/min` global και `30/min` στα search endpoints).
- Το Flask debug mode ρυθμίζεται πλέον μέσω μεταβλητής περιβάλλοντος `FLASK_DEBUG`.
- Τα αρχεία υποβολής υπάρχουν στον φάκελο `deliverables/`.

---

## 📂 Δομή κώδικα & χρήση

### 1. Δημιουργία βάσης & ETL pipeline

Τα scripts εξαγωγής, καθαρισμού και φόρτωσης δεδομένων βρίσκονται στον φάκελο `/etl` και πρέπει να εκτελούνται με τη σειρά:

```bash
mysql -u root -P 3307 -e "CREATE DATABASE biblio_db;"
mysql -u root -P 3307 biblio_db < etl/01_create_schema.sql
python etl/02_clean_inproceedings.py
python etl/03_clean_articles.py
python etl/04_load_lookups.py
python etl/05_match_conferences.py
python etl/06_match_journals.py
python etl/07_load_papers.py
mysql -u root -P 3307 biblio_db < etl/08_create_views.sql
mysql -u root -P 3307 biblio_db < etl/09_search_indexes.sql
mysql -u root -P 3307 biblio_db < etl/09_performance_optimization.sql
```

> Το `etl/07_load_papers.py` είναι το πιο χρονοβόρο βήμα και μπορεί να χρειαστεί περίπου 35 λεπτά για πλήρη φόρτωση περίπου 2,5 εκατομμυρίων γραμμών.

Υπάρχει επίσης script backup στο `etl/09_backup.bat`.

### Ασφαλές backup / rebuild / restore

Για τοπική εργασία με MariaDB `10.4.32` στο `localhost:3307`, προτείνεται πάντα λογικό backup πριν από αλλαγές σε schema ή ETL.

1. Δημιουργία backup:
   - `etl\\09_backup.bat`
   - Τα dumps αποθηκεύονται στο `data\\backups\\` ως `biblio_db_backup_YYYY-MM-DD_HH-MM-SS.sql`.
2. Rebuild από ETL όταν χρειάζεται καθαρή βάση από τα αρχικά αρχεία:
   - Διαγραφή και επαναδημιουργία της `biblio_db`.
   - Εκτέλεση των ETL βημάτων με τη σειρά.
3. Restore από γνωστό καλό dump όταν χρειάζεται ασφαλής επιστροφή σε προηγούμενη κατάσταση.
4. Έλεγχος μετά από rebuild ή restore:

```bash
python scripts/validate_etl.py
```

### Πολιτική αντιστοίχισης venues

- Η αντιστοίχιση venues είναι best-effort.
- Αν ένα DBLP `booktitle` ή `journal` δεν αντιστοιχιστεί σε iCORE/Kaggle venue, το paper παραμένει στον τελικό πίνακα `papers`.
- Τα μη αντιστοιχισμένα conference papers αποθηκεύονται με `type = 'conference'` και `conf_id = NULL`.
- Τα μη αντιστοιχισμένα journal papers αποθηκεύονται με `type = 'journal'` και `journal_id = NULL`.
- Αυτό αποτρέπει την υπομέτρηση δραστηριότητας συγγραφέων και ετών λόγω ελλιπών λεξικών venues.

---

## 🚀 Εκκίνηση Flask backend

Η REST εφαρμογή βρίσκεται στον φάκελο `/backend` και σερβίρει το static HTML frontend από τον φάκελο `/frontend`.

```bash
# Εκτέλεση από το root του repository
python backend/app.py
```

Το dashboard ανοίγει τοπικά στο:

```text
http://localhost:5000
```

### Ενδεικτικά REST endpoints

- `GET /health` — JSON healthcheck.
- `GET /api/conference/` — λίστα ranked conferences.
- `GET /api/journal/<id>/profile` — JSON bundle με δεδομένα προφίλ και aggregated στατιστικά.
- `GET /api/author/<id>/papers` — αναζήτηση όλων των δημοσιεύσεων συγκεκριμένου συγγραφέα.

---

## 🧭 Σημειώσεις χρήσης

- Πριν από αλλαγές στη βάση ή στο ETL, δημιουργήστε backup.
- Χρησιμοποιήστε rebuild όταν αλλάζει ο ETL κώδικας ή τα source mappings.
- Χρησιμοποιήστε restore όταν χρειάζεται γρήγορη επιστροφή σε γνωστή σωστή κατάσταση.
- Τα στατιστικά δραστηριότητας βασίζονται κυρίως στο DBLP dataset. Κάποια περιοδικά που υπάρχουν μόνο στη βάση κατατάξεων Kaggle μπορεί να εμφανίζονται με μηδενικές δημοσιεύσεις αν δεν καλύπτονται από DBLP.
