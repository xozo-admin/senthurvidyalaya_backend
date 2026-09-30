# Cheran School backend

Django REST API for the Cheran School application.

## Local setup

Run the following commands from `cheran-school-back` in PowerShell:

```powershell
# Create the virtual environment (skip this if venv already exists)
py -m venv venv

# Activate it and install the dependencies
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# Create/update the database schema
python manage.py migrate
```

The default database is SQLite (`db.sqlite3`). Database and other environment
settings can be overridden in `.env`; see `config/settings.py` for the
supported variable names.

## Create the supplied users and demo data

The repository includes `full_data.json`. It contains the related school data
and 292 login accounts: 2 admins, 145 students, 122 teachers, and 23 staff
users. Load it after running migrations:

```powershell
python manage.py loaddata full_data.json
```

The command is intended for an empty local database. To confirm that the users
were created:

```powershell
python manage.py shell -c "from django.db.models import Count; from accounts.models import User; print(User.objects.count()); print(list(User.objects.values('user_type').annotate(total=Count('id')).order_by('user_type')))"
```

## Demo login credentials

These credentials are for local/demo use only. Every listed password is the
phone number stored on that user account.

Some fixture accounts originally have hashed passwords that cannot be read
from the fixture. After every fresh `loaddata`, normalize all local demo
passwords to the phone numbers shown below:

```powershell
python manage.py shell -c "from accounts.models import User; users=User.objects.exclude(phone__isnull=True).exclude(phone=''); [(u.set_password(u.phone), u.save(update_fields=['password'])) for u in users]"
```

All 292 credentials are listed below. The normalization command above has
already been run on the current local database.

### Admin accounts (2)

| Username | Password |
| --- | --- |
| `mock_admin_finance` | `9000000001` |
| `xozo@gmail.com` | `9999999999` |

### Staff accounts (23)

| Username | Password |
| --- | --- |
| `3001` | `1234567890` |
| `3002` | `1234567890` |
| `3003` | `9876543232` |
| `HWD001` | `9000001001` |
| `HWD002` | `9000001002` |
| `mock_staff_admin_staff` | `9200000001` |
| `mock_staff_external_staff` | `9200000006` |
| `mock_staff_finance_staff` | `9200000002` |
| `mock_staff_it_staff` | `9200000003` |
| `mock_staff_operations_staff` | `9200000004` |
| `mock_staff_transport_staff` | `9200000005` |
| `STFADM001` | `9001101001` |
| `STFADM002` | `9001101002` |
| `STFEXT001` | `9001106001` |
| `STFEXT002` | `9001106002` |
| `STFFIN001` | `9001102001` |
| `STFFIN002` | `9001102002` |
| `STFIT001` | `9001103001` |
| `STFIT002` | `9001103002` |
| `STFOPS001` | `9001104001` |
| `STFOPS002` | `9001104002` |
| `STFTRN001` | `9001105001` |
| `STFTRN002` | `9001105002` |

### Student accounts (145)

| Username | Password |
| --- | --- |
| `1001` | `1234567890` |
| `1002` | `9876543213` |
| `1003` | `9876543215` |
| `1004` | `987654321` |
| `1005` | `1234567891` |
| `4000` | `1234567890` |
| `4017` | `1234567890` |
| `4031` | `1234567890` |
| `5000` | `1234567890` |
| `mock_student_001` | `9300000001` |
| `mock_student_002` | `9300000002` |
| `mock_student_003` | `9300000003` |
| `mock_student_004` | `9300000004` |
| `mock_student_005` | `9300000005` |
| `mock_student_006` | `9300000006` |
| `mock_student_007` | `9300000007` |
| `mock_student_008` | `9300000008` |
| `mock_student_009` | `9300000009` |
| `mock_student_010` | `9300000010` |
| `mock_student_011` | `9300000011` |
| `mock_student_012` | `9300000012` |
| `mock_student_013` | `9300000013` |
| `mock_student_014` | `9300000014` |
| `mock_student_015` | `9300000015` |
| `mock_student_016` | `9300000016` |
| `mock_student_017` | `9300000017` |
| `mock_student_018` | `9300000018` |
| `mock_student_019` | `9300000019` |
| `mock_student_020` | `9300000020` |
| `mock_student_021` | `9300000021` |
| `mock_student_022` | `9300000022` |
| `mock_student_023` | `9300000023` |
| `mock_student_024` | `9300000024` |
| `mock_student_025` | `9300000025` |
| `mock_student_026` | `9300000026` |
| `mock_student_027` | `9300000027` |
| `mock_student_028` | `9300000028` |
| `mock_student_029` | `9300000029` |
| `mock_student_030` | `9300000030` |
| `mock_student_031` | `9300000031` |
| `mock_student_032` | `9300000032` |
| `mock_student_033` | `9300000033` |
| `mock_student_034` | `9300000034` |
| `mock_student_035` | `9300000035` |
| `mock_student_036` | `9300000036` |
| `STU01A01` | `9801011001` |
| `STU01A02` | `9801021002` |
| `STU01A03` | `9801031003` |
| `STU01A04` | `9801041004` |
| `STU01A05` | `9801051005` |
| `STU01B01` | `9801011006` |
| `STU01B02` | `9801021007` |
| `STU01B03` | `9801031008` |
| `STU01B04` | `9801041009` |
| `STU01B05` | `9801051010` |
| `STU02A01` | `9802011011` |
| `STU02A02` | `9802021012` |
| `STU02A03` | `9802031013` |
| `STU02A04` | `9802041014` |
| `STU02A05` | `9802051015` |
| `STU02B01` | `9802011016` |
| `STU02B02` | `9802021017` |
| `STU02B03` | `9802031018` |
| `STU02B04` | `9802041019` |
| `STU02B05` | `9802051020` |
| `STU03A01` | `9803011021` |
| `STU03A02` | `9803021022` |
| `STU03A03` | `9803031023` |
| `STU03A04` | `9803041024` |
| `STU03A05` | `9803051025` |
| `STU03B01` | `9803011026` |
| `STU03B02` | `9803021027` |
| `STU03B03` | `9803031028` |
| `STU03B04` | `9803041029` |
| `STU03B05` | `9803051030` |
| `STU04A01` | `9804011031` |
| `STU04A02` | `9804021032` |
| `STU04A03` | `9804031033` |
| `STU04A04` | `9804041034` |
| `STU04A05` | `9804051035` |
| `STU04B01` | `9804011036` |
| `STU04B02` | `9804021037` |
| `STU04B03` | `9804031038` |
| `STU04B04` | `9804041039` |
| `STU04B05` | `9804051040` |
| `STU05A01` | `9805011041` |
| `STU05A02` | `9805021042` |
| `STU05A03` | `9805031043` |
| `STU05A04` | `9805041044` |
| `STU05A05` | `9805051045` |
| `STU05B01` | `9805011046` |
| `STU05B02` | `9805021047` |
| `STU05B03` | `9805031048` |
| `STU05B04` | `9805041049` |
| `STU05B05` | `9805051050` |
| `STU06A01` | `9806011051` |
| `STU06A02` | `9806021052` |
| `STU06A03` | `9806031053` |
| `STU06A04` | `9806041054` |
| `STU06A05` | `9806051055` |
| `STU06B01` | `9806011056` |
| `STU06B02` | `9806021057` |
| `STU06B03` | `9806031058` |
| `STU06B04` | `9806041059` |
| `STU06B05` | `9806051060` |
| `STU07A01` | `9807011061` |
| `STU07A02` | `9807021062` |
| `STU07A03` | `9807031063` |
| `STU07A04` | `9807041064` |
| `STU07A05` | `9807051065` |
| `STU07B01` | `9807011066` |
| `STU07B02` | `9807021067` |
| `STU07B03` | `9807031068` |
| `STU07B04` | `9807041069` |
| `STU07B05` | `9807051070` |
| `STU08A01` | `9808011071` |
| `STU08A02` | `9808021072` |
| `STU08A03` | `9808031073` |
| `STU08A04` | `9808041074` |
| `STU08A05` | `9808051075` |
| `STU08B01` | `9808011076` |
| `STU08B02` | `9808021077` |
| `STU08B03` | `9808031078` |
| `STU08B04` | `9808041079` |
| `STU08B05` | `9808051080` |
| `STU09A01` | `9809011081` |
| `STU09A02` | `9809021082` |
| `STU09A03` | `9809031083` |
| `STU09A04` | `9809041084` |
| `STU09A05` | `9809051085` |
| `STU09B01` | `9809011086` |
| `STU09B02` | `9809021087` |
| `STU09B03` | `9809031088` |
| `STU09B04` | `9809041089` |
| `STU09B05` | `9809051090` |
| `STU10A01` | `9810011091` |
| `STU10A02` | `9810021092` |
| `STU10A03` | `9810031093` |
| `STU10A04` | `9810041094` |
| `STU10A05` | `9810051095` |
| `STU10B01` | `9810011096` |
| `STU10B02` | `9810021097` |
| `STU10B03` | `9810031098` |
| `STU10B04` | `9810041099` |
| `STU10B05` | `9810051100` |

### Teacher accounts (122)

| Username | Password |
| --- | --- |
| `2001` | `1234567890` |
| `2002` | `9876543221` |
| `2003` | `9876543222` |
| `mock_teacher_01` | `9100000001` |
| `mock_teacher_02` | `9100000002` |
| `mock_teacher_03` | `9100000003` |
| `mock_teacher_04` | `9100000004` |
| `mock_teacher_05` | `9100000005` |
| `mock_teacher_06` | `9100000006` |
| `mock_teacher_07` | `9100000007` |
| `mock_teacher_08` | `9100000008` |
| `TCHCT01A` | `960111100` |
| `TCHCT01B` | `960121101` |
| `TCHCT02A` | `960211102` |
| `TCHCT02B` | `960221103` |
| `TCHCT03A` | `960311104` |
| `TCHCT03B` | `960321105` |
| `TCHCT04A` | `960411106` |
| `TCHCT04B` | `960421107` |
| `TCHCT05A` | `960511108` |
| `TCHCT05B` | `960521109` |
| `TCHCT06A` | `960611110` |
| `TCHCT06B` | `960621111` |
| `TCHCT07A` | `960711112` |
| `TCHCT07B` | `960721113` |
| `TCHCT08A` | `960811114` |
| `TCHCT08B` | `960821115` |
| `TCHCT09A` | `960911116` |
| `TCHCT09B` | `960921117` |
| `TCHCT10A` | `961011118` |
| `TCHCT10B` | `961021119` |
| `TCHSP001` | `950172200` |
| `TCHSP002` | `950272201` |
| `TCHSP003` | `950372202` |
| `TCHSP004` | `950472203` |
| `TCHSP005` | `950572204` |
| `TCHSP006` | `950672205` |
| `TCHSP007` | `950772206` |
| `TCHSP008` | `950872207` |
| `TCHSP009` | `950972208` |
| `TCHSP010` | `951072209` |
| `TCHSP011` | `951172210` |
| `TCHSP012` | `951272211` |
| `TCHSP013` | `951372212` |
| `TCHSP014` | `951472213` |
| `TCHSP015` | `951572214` |
| `TCHSP016` | `951672215` |
| `TCHSP017` | `951772216` |
| `TCHSP018` | `951872217` |
| `TCHSP019` | `951972218` |
| `TCHSP020` | `952072219` |
| `TCHSP021` | `952172220` |
| `TCHTT06A01` | `940613000` |
| `TCHTT06A02` | `940613001` |
| `TCHTT06A03` | `940613002` |
| `TCHTT06A04` | `940613003` |
| `TCHTT06A05` | `940613004` |
| `TCHTT06A06` | `940613005` |
| `TCHTT06A07` | `940613006` |
| `TCHTT06B01` | `940623000` |
| `TCHTT06B02` | `940623001` |
| `TCHTT06B03` | `940623002` |
| `TCHTT06B04` | `940623003` |
| `TCHTT06B05` | `940623004` |
| `TCHTT06B06` | `940623005` |
| `TCHTT06B07` | `940623006` |
| `TCHTT07A01` | `940713000` |
| `TCHTT07A02` | `940713001` |
| `TCHTT07A03` | `940713002` |
| `TCHTT07A04` | `940713003` |
| `TCHTT07A05` | `940713004` |
| `TCHTT07A06` | `940713005` |
| `TCHTT07A07` | `940713006` |
| `TCHTT07B01` | `940723000` |
| `TCHTT07B02` | `940723001` |
| `TCHTT07B03` | `940723002` |
| `TCHTT07B04` | `940723003` |
| `TCHTT07B05` | `940723004` |
| `TCHTT07B06` | `940723005` |
| `TCHTT07B07` | `940723006` |
| `TCHTT08A01` | `940813000` |
| `TCHTT08A02` | `940813001` |
| `TCHTT08A03` | `940813002` |
| `TCHTT08A04` | `940813003` |
| `TCHTT08A05` | `940813004` |
| `TCHTT08A06` | `940813005` |
| `TCHTT08A07` | `940813006` |
| `TCHTT08B01` | `940823000` |
| `TCHTT08B02` | `940823001` |
| `TCHTT08B03` | `940823002` |
| `TCHTT08B04` | `940823003` |
| `TCHTT08B05` | `940823004` |
| `TCHTT08B06` | `940823005` |
| `TCHTT08B07` | `940823006` |
| `TCHTT09A01` | `940913000` |
| `TCHTT09A02` | `940913001` |
| `TCHTT09A03` | `940913002` |
| `TCHTT09A04` | `940913003` |
| `TCHTT09A05` | `940913004` |
| `TCHTT09A06` | `940913005` |
| `TCHTT09A07` | `940913006` |
| `TCHTT09B01` | `940923000` |
| `TCHTT09B02` | `940923001` |
| `TCHTT09B03` | `940923002` |
| `TCHTT09B04` | `940923003` |
| `TCHTT09B05` | `940923004` |
| `TCHTT09B06` | `940923005` |
| `TCHTT09B07` | `940923006` |
| `TCHTT10A01` | `941013000` |
| `TCHTT10A02` | `941013001` |
| `TCHTT10A03` | `941013002` |
| `TCHTT10A04` | `941013003` |
| `TCHTT10A05` | `941013004` |
| `TCHTT10A06` | `941013005` |
| `TCHTT10A07` | `941013006` |
| `TCHTT10B01` | `941023000` |
| `TCHTT10B02` | `941023001` |
| `TCHTT10B03` | `941023002` |
| `TCHTT10B04` | `941023003` |
| `TCHTT10B05` | `941023004` |
| `TCHTT10B06` | `941023005` |
| `TCHTT10B07` | `941023006` |


To create a new Django administrator instead of loading demo data:

```powershell
python manage.py createsuperuser
```

Note that `createsuperuser` creates access to Django's `/admin/` site. The
application's school roles and linked profiles are normally created through
the REST API or the school-admin UI.

## Run the API

```powershell
python manage.py runserver
```

The API is available at `http://127.0.0.1:8000/`. Authentication endpoints
include:

- `POST /api/accounts/login/`
- `POST /api/accounts/verify-otp/`
- `POST /api/accounts/super-admin-register/`
- `POST /api/accounts/admin-register/`

The frontend runs separately from `../cheran-school-front`:

```powershell
Set-Location ..\cheran-school-front
npm install
npm run dev
```

Open `http://localhost:3000/`.

## Checks

```powershell
python manage.py check
python manage.py test
```
