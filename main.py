#!/usr/bin/env python3
# =============================================================================
#  EPI MANAGER  ·  ISOFU  ·  Architecte : Roger Fernando
#  Gestion EPI — Sites de Revêtement Industriel
#  Point d'entrée modulaire
# =============================================================================

from core.database import initialize_database
from gui.app import PPEVaultApp

def main() -> None:
    # Hydratation initiale de la base de données (si vide)
    initialize_database()
    
    # Lancement du processus Tkinter
    app = PPEVaultApp()
    app.mainloop()

if __name__ == "__main__":
    main()