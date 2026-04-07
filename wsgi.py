from main import app
from main import delete_all
from threading import Thread

# run the server
def run():
    try:
        delete_all()
    except:
        pass
    app.run(ssl_context=('cert/fullchain.pem', 'cert/privkey.pem'))

# keep the server running
def keep_alive():
    t = Thread(target=run)
    t.start()

# keep the server running
keep_alive()