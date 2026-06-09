import requests
TOKEN = "8803348427:AAFTlW6iBNGwtM3SjfbF0488xfw-IbyRFvY"
print(requests.get("https://api.telegram.org/bot" + TOKEN + "/getMe").json())
print(requests.get("https://api.telegram.org/bot" + TOKEN + "/getUpdates").json())
