### Implementation
First challenge is to implement an information retrieval system. The output will be text to start. The kinds of questions I will ask the ‘memory jogger’ are of the kind:

1. Fetching information
2. Summarising information. Summarisation requires first retrieving

Queries can be single intent or multi-intent. Example queries could be:

1. How many times did Leo X in the past Y?
2. When was the last time that Leo did X?
3. What was the colour of Leo’s last stool?
4. How much did Leo eat today? (if no ML should be time)
5. How long did Leo feed for on Wednesday?
6. Tell me what time Leo fed on Wednesday and Thursday?
7. When did Leo sleep on Wednesday and when did he wake up in the morning?
8. When did Leo sleep on Wednesday and when did he eat first the next day?
9. What time does Leo normally sleep in the morning?
10. When is Leo’s bedtime?
11. What time does Leo normally wake-up in the morning?
    1. Challenging because needs to infer what morning is and the data doesn’t really contain anything but time. Could solve by giving the model in the prompt a range for what would be morning e.g. 5-9am. Most babies are going to be waking up then. 
        1. The agent should be able to go and figure things out like that.
12. What time did we X Leo last? 
13. On Monday, did we give Leo a bottle?
14. On Monday, did we give Leo a bath?
15. What time did we bath Leo last Tuesday?
16. Summarise my babies wake up patterns this week.
17. What kind of stools did Leo have yesterday?

Challenges:

1. Requires resolving time and date relative to user’s current time e.g. yesterday
2. Requires retrieving data over a range which could be large
3. Requires resolving time and date references with multiple options e.g. “when did he wake up in the morning”
4. Requires intent extraction e.g. “when did he eat first *the next day*”