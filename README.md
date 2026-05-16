# BOTtel — Telegram Media Downloader Bot

بوت تيليجرام لتحميل الوسائط من الروابط العامة المصرح بها باستخدام yt-dlp.

## التشغيل

1. ثبت المتطلبات: pip install -r requirements.txt
2. عدل ملف .env وضع TELEGRAM_TOKEN.
3. شغل البوت: python main.py

## ملف .env

TELEGRAM_TOKEN=توكن_البوت
ADMIN_IDS=رقمك_في_تيليجرام
MAX_UPLOAD_MB=45
RATE_LIMIT_SECONDS=10

## أوامر المستخدم

/start
/help
/about
/legal
/whoami

## أوامر المالك

/admin
/settext start النص الجديد
/setsetting max_upload_mb 45
/addbutton اسم الزر | https://example.com
/delbutton 1

## تنبيه قانوني

استخدم البوت فقط للمحتوى الذي تملكه أو لديك حق تحميله. لا يدعم تجاوز DRM أو الحسابات الخاصة أو الاشتراكات أو تسجيل الدخول.
