# pitvisor

pitvisor provides F1 data through graphs for fans to analyze.

### [pitvisor.ojee.net](https://pitvisor.ojee.net)

[Add pitvisor to your discord server](https://discord.com/oauth2/authorize?client_id=892359806898303036&permissions=534723947584&scope=bot)  
[pitvisor Support Server](https://discord.com/invite/uXY5Va4Jbb)  
[pitvisor ToS](https://pitvisor.ojee.net/tos)  
[pitvisor Privacy Policy](https://pitvisor.ojee.net/priv)  

# Requirements

Run `pip install -r requirements.txt` to install dependencies.

# Running

- To enable cache make a folder named `doc_cache` in the main directory.
- To run the server, run `main.py`.
- To generate graphs you can either, send a `POST` request to `/` with the proper inputs.
- Alternatively, you can use the functions directly in `funcs.py`.
- To update data, send an empty `POST` request to `/update`.
- Alternatively, you can update data using the functions in `update.py`.
- All the plotting functions are in `funcs.py`.
- `funcs.py` uses the built in cache in the fastf1 library.

# Data

Data is from the [FastF1](https://github.com/theOehrly/Fast-F1) and [Jolpica F1](https://github.com/jolpica/jolpica-f1).

# Supporting the Project

If you want to support the development of pitvisor, you can sponsor me on GitHub or buy me a coffee.

<a href="https://www.buymeacoffee.com/ojee">
  <img alt="buymeacoffee" title="Buy me a coffee" src="https://img.shields.io/badge/buy_me_a_coffee-FFE01A?style=for-the-badge&logo=buymeacoffee&logoColor=black"/></a>

# Status

<!-- <img alt='status' title='Status' src='https://custom-icon-badges.demolab.com/badge/-server%20status:%20OFFLINE-aa0000?style=for-the-badge&logo=server&logoColor=white'/> -->
<img alt='status' title='Status' src='https://custom-icon-badges.demolab.com/badge/-server%20status:%20ONLINE-00aa00?style=for-the-badge&logo=server&logoColor=white'/>
