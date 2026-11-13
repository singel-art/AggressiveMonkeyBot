//+------------------------------------------------------------------+
//| AMB_ExportBars.mq5                                               |
//| Exporte les bougies M1 d'un symbole vers un fichier CSV lu par   |
//| le module Python amb_research (etude statistique des impulsions).|
//|                                                                  |
//| Script en lecture seule : il n'ouvre, ne modifie et ne ferme     |
//| aucun ordre.                                                     |
//|                                                                  |
//| NON COMPILE par son auteur : a compiler dans MetaEditor.         |
//|                                                                  |
//| Les heures ecrites sont celles du serveur de trading, telles que |
//| MT5 les stocke. Elles ne sont PAS converties en UTC ici : l'ecart|
//| du serveur change avec les saisons, et le script ne connait que  |
//| celui du jour. La conversion est faite et verifiee cote Python.  |
//+------------------------------------------------------------------+
#property script_show_inputs
#property version "1.00"

input string          InpSymbol   = "";                      // Symbole (vide = celui du graphique)
input datetime        InpFrom     = D'2023.01.01 00:00';     // Debut de l'historique
input string          InpFilePrefix = "amb_bars";            // Prefixe des fichiers (dossier Common\Files)

void OnStart()
  {
   string symbol = (InpSymbol == "") ? _Symbol : InpSymbol;
   if(!SymbolSelect(symbol, true))
     {
      PrintFormat("AMB_ExportBars : symbole %s introuvable", symbol);
      return;
     }
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
     {
      Print("AMB_ExportBars : terminal non connecte, export annule.");
      return;
     }
   string base = InpFilePrefix + "_" + symbol + "_M1";
   datetime end = TimeTradeServer();
   long offset = (long)(TimeTradeServer() - TimeGMT());
   offset = (long)MathRound((double)offset / 1800.0) * 1800;

   int handle = FileOpen(base + ".csv", FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON, ';', CP_UTF8);
   if(handle == INVALID_HANDLE)
     {
      PrintFormat("AMB_ExportBars : ouverture du fichier impossible (erreur %d)", GetLastError());
      return;
     }
   FileWriteString(handle, "time_server;open;high;low;close;tick_volume;spread_points;real_volume\n");

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   const long CHUNK = 31 * 86400;                              // tranches d'environ un mois
   long written = 0;
   datetime first = 0, last = 0;
   bool failed = false;
   datetime from = InpFrom;
   while(from < end && !IsStopped())
     {
      datetime to = (datetime)MathMin((long)from + CHUNK, (long)end);
      MqlRates rates[];
      ResetLastError();
      int n = CopyRates(symbol, PERIOD_M1, from, to, rates);
      if(n < 0)
        {
         // Historique pas encore telecharge pour cette tranche : on le signale et on continue.
         PrintFormat("AMB_ExportBars : aucune donnee entre %s et %s (erreur %d)",
                     TimeToString(from, TIME_DATE), TimeToString(to, TIME_DATE), GetLastError());
         from = to;
         continue;
        }
      for(int i = 0; i < n; i++)
        {
         // Une bougie placee exactement sur la borne appartient a la tranche suivante.
         if(rates[i].time >= to && to < end)
            continue;
         if(rates[i].time <= last)
            continue;
         string line = TimeToString(rates[i].time, TIME_DATE | TIME_SECONDS) + ";" +
                       DoubleToString(rates[i].open, digits) + ";" +
                       DoubleToString(rates[i].high, digits) + ";" +
                       DoubleToString(rates[i].low, digits) + ";" +
                       DoubleToString(rates[i].close, digits) + ";" +
                       IntegerToString(rates[i].tick_volume) + ";" +
                       IntegerToString(rates[i].spread) + ";" +
                       IntegerToString(rates[i].real_volume) + "\n";
         if(FileWriteString(handle, line) == 0)
           {
            failed = true;
            break;
           }
         if(first == 0)
            first = rates[i].time;
         last = rates[i].time;
         written++;
        }
      if(failed)
         break;
      from = to;
     }
   FileClose(handle);
   if(failed || IsStopped() || written == 0)
     {
      FileDelete(base + ".csv", FILE_COMMON);
      Print("AMB_ExportBars : export interrompu ou vide, fichier supprime.");
      return;
     }

   // Fiche du symbole : indispensable pour convertir les points en prix et en argent.
   int meta = FileOpen(base + ".meta.csv", FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON, ';', CP_UTF8);
   if(meta != INVALID_HANDLE)
     {
      FileWriteString(meta, "key;value\n");
      FileWriteString(meta, "symbol;" + symbol + "\n");
      FileWriteString(meta, "timeframe;M1\n");
      FileWriteString(meta, "point;" + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_POINT), 8) + "\n");
      FileWriteString(meta, "digits;" + IntegerToString(digits) + "\n");
      FileWriteString(meta, "tick_size;" + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE), 8) + "\n");
      FileWriteString(meta, "tick_value;" + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE), 8) + "\n");
      FileWriteString(meta, "contract_size;" + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE), 4) + "\n");
      FileWriteString(meta, "volume_min;" + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN), 4) + "\n");
      FileWriteString(meta, "spread_floating;" + IntegerToString(SymbolInfoInteger(symbol, SYMBOL_SPREAD_FLOAT)) + "\n");
      FileWriteString(meta, "server;" + AccountInfoString(ACCOUNT_SERVER) + "\n");
      FileWriteString(meta, "utc_offset_s_at_export;" + IntegerToString(offset) + "\n");
      FileWriteString(meta, "exported_at_server;" + TimeToString(end, TIME_DATE | TIME_SECONDS) + "\n");
      FileWriteString(meta, "first_bar_server;" + TimeToString(first, TIME_DATE | TIME_SECONDS) + "\n");
      FileWriteString(meta, "last_bar_server;" + TimeToString(last, TIME_DATE | TIME_SECONDS) + "\n");
      FileWriteString(meta, "bars;" + IntegerToString(written) + "\n");
      FileClose(meta);
     }
   PrintFormat("AMB_ExportBars : %I64d bougies ecrites dans Common\\Files\\%s.csv (du %s au %s)",
               written, base, TimeToString(first, TIME_DATE), TimeToString(last, TIME_DATE));
  }
//+------------------------------------------------------------------+
