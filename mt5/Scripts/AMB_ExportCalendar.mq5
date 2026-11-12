//+------------------------------------------------------------------+
//| AMB_ExportCalendar.mq5                                           |
//| Exporte le calendrier economique du terminal vers un fichier CSV |
//| lu par le module Python market_intelligence.                     |
//|                                                                  |
//| Script en lecture seule : il n'ouvre, ne modifie et ne ferme     |
//| aucun ordre.                                                     |
//|                                                                  |
//| NON COMPILE par son auteur : a compiler dans MetaEditor et a     |
//| valider en suivant docs/MT5_CALENDAR.md. Les fonctions et les    |
//| structures utilisees ont ete relues dans la documentation MQL5   |
//| (CalendarValueHistory, MqlCalendarValue, MqlCalendarEvent).      |
//+------------------------------------------------------------------+
#property script_show_inputs
#property version "1.10"

input datetime InpFrom      = D'2015.01.01 00:00';     // Debut de l'historique
input int      InpDaysAhead = 90;                      // Jours a venir a inclure
input string   InpCurrency  = "USD";                   // Devise des evenements
input string   InpFileName  = "amb_calendar_USD.csv";  // Fichier (dossier Common\Files)

string Clean(string text)
  {
   StringReplace(text, ";", ",");
   StringReplace(text, "\n", " ");
   StringReplace(text, "\r", " ");
   return text;
  }

void OnStart()
  {
   MqlCalendarValue values[];
   datetime to = TimeTradeServer() + (datetime)(InpDaysAhead * 86400);
   int total = CalendarValueHistory(values, InpFrom, to, NULL, InpCurrency);
   if(total <= 0)
     {
      PrintFormat("AMB_ExportCalendar : aucune valeur recue (erreur %d)", GetLastError());
      return;
     }

   // Ecart actuel entre l'heure du serveur et l'heure UTC, arrondi a la demi-heure.
   // ATTENTION : cet ecart est celui d'aujourd'hui. Si le serveur change d'heure en
   // ete, les dates d'une autre saison peuvent etre decalees d'une heure. Les colonnes
   // time_server et utc_offset_s sont conservees : le module Python recoupe les heures
   // avec les communiques dates de la Fed et signale tout decalage.
   long offset = (long)(TimeTradeServer() - TimeGMT());
   offset = (long)MathRound((double)offset / 1800.0) * 1800;

   int handle = FileOpen(InpFileName, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON, ';', CP_UTF8);
   if(handle == INVALID_HANDLE)
     {
      PrintFormat("AMB_ExportCalendar : ouverture du fichier impossible (erreur %d)", GetLastError());
      return;
     }
   FileWriteString(handle, "value_id;event_id;time_utc;time_server;utc_offset_s;time_mode;currency;importance;name;actual;forecast;previous;revised_previous;unit;impact\n");

   int written = 0;
   for(int i = 0; i < total; i++)
     {
      MqlCalendarEvent ev;
      if(!CalendarEventById(values[i].event_id, ev))
         continue;
      // Une valeur absente est ecrite vide : jamais remplacee par zero.
      string actual   = values[i].HasActualValue()   ? DoubleToString(values[i].GetActualValue(),   (int)ev.digits) : "";
      string forecast = values[i].HasForecastValue() ? DoubleToString(values[i].GetForecastValue(), (int)ev.digits) : "";
      string previous = values[i].HasPreviousValue() ? DoubleToString(values[i].GetPreviousValue(), (int)ev.digits) : "";
      string revised  = values[i].HasRevisedValue()  ? DoubleToString(values[i].GetRevisedValue(),  (int)ev.digits) : "";
      datetime utc = (datetime)((long)values[i].time - offset);
      string line = IntegerToString((long)values[i].id) + ";" +
                    IntegerToString((long)ev.id) + ";" +
                    TimeToString(utc, TIME_DATE | TIME_SECONDS) + ";" +
                    TimeToString(values[i].time, TIME_DATE | TIME_SECONDS) + ";" +
                    IntegerToString(offset) + ";" +
                    EnumToString(ev.time_mode) + ";" +
                    InpCurrency + ";" +
                    IntegerToString((int)ev.importance) + ";" +
                    Clean(ev.name) + ";" +
                    actual + ";" + forecast + ";" + previous + ";" + revised + ";" +
                    EnumToString(ev.unit) + "/" + EnumToString(ev.multiplier) + ";" +
                    EnumToString(values[i].impact_type) + "\n";
      FileWriteString(handle, line);
      written++;
     }
   FileClose(handle);
   PrintFormat("AMB_ExportCalendar : %d lignes ecrites dans Common\\Files\\%s (ecart serveur-UTC applique : %d s)",
               written, InpFileName, (int)offset);
  }
//+------------------------------------------------------------------+
