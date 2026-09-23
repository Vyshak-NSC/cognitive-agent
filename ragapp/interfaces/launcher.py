import subprocess,sys
MENU='''\n=== Cognitive Persistence Agent ===\n\n1) Streamlit app\n2) Terminal chat\n0) Exit\n'''
def main():
    while True:
        print(MENU); choice=input('Select an option: ').strip()
        if choice=='1': subprocess.run([sys.executable,'-m','streamlit','run','ragapp/interfaces/streamlit_app/main.py'])
        elif choice=='2': subprocess.run([sys.executable,'-m','ragapp.interfaces.cli.terminal'])
        elif choice=='0': break
        else: print('Invalid choice.')
if __name__=='__main__': main()
