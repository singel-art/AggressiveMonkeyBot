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
#property version "1.20"

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

// Ecrit les valeurs d'une periode [from, to[. Renvoie le nombre de lignes ecrites, ou -1 en cas d'erreur.
// Le dernier appel inclut aussi la borne finale (include_end).
int ExportRange(int handle, datetime from, datetime to, bool include_end, long offset)
  {
   MqlCalendarValue values[];
   ResetLastError();
   int total = CalendarValueHistory(values, from, to, NULL, InpCurrency);
   if(total < 0)
     {
      PrintFormat("AMB_ExportCalendar : echec entre %s et %s (erreur %d)",
                  TimeToString(from, TIME_DATE), TimeToString(to, TIME_DATE), GetLastError());
      return -1;
     }
   int written = 0;
   for(int i = 0; i < total; i++)
     {
      // Une valeur placee exactement sur la borne appartient a la periode suivante :
      // elle n'est ecrite qu'une fois.
      if(values[i].time >= to && !include_end)
         continue;
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
   return written;
  }

void OnStart()
  {
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
     {
      Print("AMB_ExportCalendar : terminal non connecte. L'heure du serveur serait estimee : export annule.");
      return;
     }
   datetime end = TimeTradeServer() + (datetime)(InpDaysAhead * 86400);
   if(InpFrom >= end)
     {
      Print("AMB_ExportCalendar : la date de debut est posterieure a la date de fin.");
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

   // Lecture par tranches d'environ un an : une demande unique sur plus de dix ans
   // peut depasser le delai du calendrier (erreur 5401).
   const long CHUNK = 366 * 86400;
   int written = 0;
   bool failed = false;
   datetime from = InpFrom;
   while(from < end && !IsStopped())
     {
      datetime to = (datetime)MathMin((long)from + CHUNK, (long)end);
      int n = ExportRange(handle, from, to, to >= end, offset);
      if(n < 0)
        {
         failed = true;
         break;
        }
      written += n;
      from = to;
     }
   FileClose(handle);
   if(failed || IsStopped())
     {
      // Un export partiel serait pris pour un historique complet : il est supprime.
      FileDelete(InpFileName, FILE_COMMON);
      Print("AMB_ExportCalendar : export interrompu, fichier supprime. Relancer le script.");
      return;
     }
   PrintFormat("AMB_ExportCalendar : %d lignes ecrites dans Common\\Files\\%s (ecart serveur-UTC applique : %d s)",
               written, InpFileName, (int)offset);
  }
//+------------------------------------------------------------------+
