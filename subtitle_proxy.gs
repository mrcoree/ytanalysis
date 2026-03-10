function doGet(e) {
  var params = (e && e.parameter) ? e.parameter : {};
  var videoId = params.v || "";

  if (!videoId) {
    return ContentService.createTextOutput(JSON.stringify({error: "missing v parameter"}))
      .setMimeType(ContentService.MimeType.JSON);
  }

  try {
    // 1. YouTube 페이지를 가져와서 get_transcript params 추출
    var pageResp = UrlFetchApp.fetch("https://www.youtube.com/watch?v=" + videoId, {
      muteHttpExceptions: true,
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9"
      }
    });
    var html = pageResp.getContentText();

    // API 키 추출
    var keyMatch = html.match(/"INNERTUBE_API_KEY":"([^"]+)"/);
    var apiKey = keyMatch ? keyMatch[1] : "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8";

    // 클라이언트 버전 추출
    var verMatch = html.match(/"INNERTUBE_CLIENT_VERSION":"([^"]+)"/);
    var clientVersion = verMatch ? verMatch[1] : "2.20250101.00.00";

    // get_transcript params 추출
    var paramMatch = html.match(/"getTranscriptEndpoint":\{"params":"([^"]+)"/);
    if (!paramMatch) {
      return ContentService.createTextOutput(JSON.stringify({error: "no transcript params found"}))
        .setMimeType(ContentService.MimeType.JSON);
    }
    var transcriptParams = paramMatch[1];

    // 2. Innertube get_transcript API 호출
    var body = {
      context: {
        client: {
          clientName: "WEB",
          clientVersion: clientVersion
        }
      },
      params: transcriptParams
    };

    var transcriptResp = UrlFetchApp.fetch(
      "https://www.youtube.com/youtubei/v1/get_transcript?key=" + apiKey,
      {
        method: "post",
        contentType: "application/json",
        payload: JSON.stringify(body),
        muteHttpExceptions: true,
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        }
      }
    );

    var status = transcriptResp.getResponseCode();
    if (status !== 200) {
      return ContentService.createTextOutput(JSON.stringify({
        error: "get_transcript failed",
        status: status,
        body: transcriptResp.getContentText().substring(0, 300)
      })).setMimeType(ContentService.MimeType.JSON);
    }

    var result = JSON.parse(transcriptResp.getContentText());

    // 3. 트랜스크립트 텍스트 추출
    var actions = result.actions || [];
    if (actions.length === 0) {
      return ContentService.createTextOutput(JSON.stringify({error: "no transcript actions"}))
        .setMimeType(ContentService.MimeType.JSON);
    }

    var panel = null;
    try {
      panel = actions[0].updateEngagementPanelAction.content.transcriptRenderer.content.transcriptSearchPanelRenderer;
    } catch(ex) {
      return ContentService.createTextOutput(JSON.stringify({error: "unexpected response structure"}))
        .setMimeType(ContentService.MimeType.JSON);
    }

    var segments = panel.body.transcriptSegmentListRenderer.initialSegments || [];
    var texts = [];
    for (var i = 0; i < segments.length; i++) {
      var runs = segments[i].transcriptSegmentRenderer.snippet.runs || [];
      for (var j = 0; j < runs.length; j++) {
        var t = (runs[j].text || "").trim();
        if (t) texts.push(t);
      }
    }

    return ContentService.createTextOutput(JSON.stringify({
      video_id: videoId,
      transcript: texts.join(" ")
    })).setMimeType(ContentService.MimeType.JSON);

  } catch(err) {
    return ContentService.createTextOutput(JSON.stringify({error: err.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
