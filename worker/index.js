const ALLOWED_ORIGINS = new Set([
  "https://thereliabilityloop.com",
  "https://www.thereliabilityloop.com",
  "http://localhost:4321",
  "http://127.0.0.1:4321",
]);

function getCorsHeaders(request) {
  const origin = request.headers.get("Origin");

  if (origin && ALLOWED_ORIGINS.has(origin)) {
    return {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
      Vary: "Origin",
    };
  }

  return {};
}

const json = (request, data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...getCorsHeaders(request),
    },
  });

const cleanSlug = (value) =>
  String(value || "")
    .trim()
    .toLowerCase()
    .replace(/^\/+|\/+$/g, "")
    .slice(0, 160);

const cleanText = (value, max) =>
  String(value || "").trim().slice(0, max);

async function verifyTurnstile(request, env, token) {
  if (!token) {
    return {
      success: false,
      error: "Please complete the security check.",
    };
  }

  if (!env.TURNSTILE_SECRET) {
    console.error("TURNSTILE_SECRET is not configured.");

    return {
      success: false,
      error: "Security verification is unavailable.",
    };
  }

  const formData = new FormData();

  formData.append(
    "secret",
    env.TURNSTILE_SECRET
  );

  formData.append(
    "response",
    token
  );

  const connectingIp =
    request.headers.get("CF-Connecting-IP");

  if (connectingIp) {
    formData.append(
      "remoteip",
      connectingIp
    );
  }

  try {
    const response = await fetch(
      "https://challenges.cloudflare.com/turnstile/v0/siteverify",
      {
        method: "POST",
        body: formData,
      }
    );

    if (!response.ok) {
      console.error(
        "Turnstile verification request failed:",
        response.status
      );

      return {
        success: false,
        error: "Security verification failed.",
      };
    }

    const result = await response.json();

    if (!result.success) {
      console.error(
        "Turnstile rejected submission:",
        result["error-codes"] || []
      );

      return {
        success: false,
        error:
          "Security verification failed. Please try again.",
      };
    }

    return {
      success: true,
    };
  } catch (error) {
    console.error(
      "Turnstile verification error:",
      error
    );

    return {
      success: false,
      error:
        "Security verification failed. Please try again.",
    };
  }
}

async function getStats(
  db,
  slug,
  visitorId
) {
  const stats = await db
    .prepare(`
      SELECT views, likes
      FROM article_stats
      WHERE article_slug = ?
    `)
    .bind(slug)
    .first();

  let liked = false;

  if (visitorId) {
    const existingLike = await db
      .prepare(`
        SELECT 1
        FROM article_likes
        WHERE article_slug = ?
          AND visitor_id = ?
        LIMIT 1
      `)
      .bind(
        slug,
        visitorId
      )
      .first();

    liked = Boolean(existingLike);
  }

  const comments = await db
    .prepare(`
      SELECT
        id,
        name,
        body,
        created_at
      FROM comments
      WHERE article_slug = ?
        AND status = 'approved'
      ORDER BY created_at ASC
    `)
    .bind(slug)
    .all();

  return {
    views: Number(
      stats?.views || 0
    ),

    likes: Number(
      stats?.likes || 0
    ),

    liked,

    comments:
      comments.results || [],
  };
}

export default {
  async fetch(request, env) {
    const url =
      new URL(request.url);

    /*
     * ---------------------------------------------------------
     * CORS PREFLIGHT
     * ---------------------------------------------------------
     */
    if (
      request.method === "OPTIONS"
    ) {
      const origin =
        request.headers.get(
          "Origin"
        );

      if (
        !origin ||
        !ALLOWED_ORIGINS.has(
          origin
        )
      ) {
        return new Response(
          null,
          {
            status: 403,
          }
        );
      }

      return new Response(
        null,
        {
          status: 204,

          headers:
            getCorsHeaders(
              request
            ),
        }
      );
    }

    try {
      /*
       * -------------------------------------------------------
       * GET ENGAGEMENT
       * -------------------------------------------------------
       */
      if (
        request.method === "GET" &&
        url.pathname ===
          "/api/engagement"
      ) {
        const slug =
          cleanSlug(
            url.searchParams.get(
              "slug"
            )
          );

        const visitorId =
          cleanText(
            url.searchParams.get(
              "visitorId"
            ),
            100
          );

        if (!slug) {
          return json(
            request,
            {
              error:
                "Article slug is required.",
            },
            400
          );
        }

        const data =
          await getStats(
            env.reliability_loop_db,
            slug,
            visitorId
          );

        return json(
          request,
          data
        );
      }

      /*
       * -------------------------------------------------------
       * RECORD VIEW
       * -------------------------------------------------------
       */
      if (
        request.method === "POST" &&
        url.pathname ===
          "/api/view"
      ) {
        const body =
          await request.json();

        const slug =
          cleanSlug(
            body.slug
          );

        const visitorId =
          cleanText(
            body.visitorId,
            100
          );

        if (
          !slug ||
          !visitorId
        ) {
          return json(
            request,
            {
              error:
                "Article slug and visitor ID are required.",
            },
            400
          );
        }

        await env
          .reliability_loop_db
          .prepare(`
            INSERT INTO article_stats (
              article_slug,
              views,
              likes,
              updated_at
            )
            VALUES (
              ?,
              0,
              0,
              CURRENT_TIMESTAMP
            )
            ON CONFLICT(article_slug)
            DO NOTHING
          `)
          .bind(slug)
          .run();

        const inserted =
          await env
            .reliability_loop_db
            .prepare(`
              INSERT OR IGNORE
              INTO article_views (
                article_slug,
                visitor_id
              )
              VALUES (?, ?)
            `)
            .bind(
              slug,
              visitorId
            )
            .run();

        if (
          inserted.meta?.changes ===
          1
        ) {
          await env
            .reliability_loop_db
            .prepare(`
              UPDATE article_stats
              SET
                views = views + 1,
                updated_at =
                  CURRENT_TIMESTAMP
              WHERE article_slug = ?
            `)
            .bind(slug)
            .run();
        }

        const data =
          await getStats(
            env.reliability_loop_db,
            slug,
            visitorId
          );

        return json(
          request,
          data
        );
      }

      /*
       * -------------------------------------------------------
       * TOGGLE LIKE
       * -------------------------------------------------------
       */
      if (
        request.method === "POST" &&
        url.pathname ===
          "/api/like"
      ) {
        const body =
          await request.json();

        const slug =
          cleanSlug(
            body.slug
          );

        const visitorId =
          cleanText(
            body.visitorId,
            100
          );

        if (
          !slug ||
          !visitorId
        ) {
          return json(
            request,
            {
              error:
                "Article slug and visitor ID are required.",
            },
            400
          );
        }

        await env
          .reliability_loop_db
          .prepare(`
            INSERT INTO article_stats (
              article_slug,
              views,
              likes,
              updated_at
            )
            VALUES (
              ?,
              0,
              0,
              CURRENT_TIMESTAMP
            )
            ON CONFLICT(article_slug)
            DO NOTHING
          `)
          .bind(slug)
          .run();

        const existing =
          await env
            .reliability_loop_db
            .prepare(`
              SELECT 1
              FROM article_likes
              WHERE article_slug = ?
                AND visitor_id = ?
              LIMIT 1
            `)
            .bind(
              slug,
              visitorId
            )
            .first();

        if (existing) {
          await env
            .reliability_loop_db
            .batch([
              env
                .reliability_loop_db
                .prepare(`
                  DELETE FROM article_likes
                  WHERE article_slug = ?
                    AND visitor_id = ?
                `)
                .bind(
                  slug,
                  visitorId
                ),

              env
                .reliability_loop_db
                .prepare(`
                  UPDATE article_stats
                  SET
                    likes =
                      MAX(
                        likes - 1,
                        0
                      ),
                    updated_at =
                      CURRENT_TIMESTAMP
                  WHERE article_slug = ?
                `)
                .bind(slug),
            ]);
        } else {
          await env
            .reliability_loop_db
            .batch([
              env
                .reliability_loop_db
                .prepare(`
                  INSERT INTO article_likes (
                    article_slug,
                    visitor_id
                  )
                  VALUES (?, ?)
                `)
                .bind(
                  slug,
                  visitorId
                ),

              env
                .reliability_loop_db
                .prepare(`
                  UPDATE article_stats
                  SET
                    likes =
                      likes + 1,
                    updated_at =
                      CURRENT_TIMESTAMP
                  WHERE article_slug = ?
                `)
                .bind(slug),
            ]);
        }

        const data =
          await getStats(
            env.reliability_loop_db,
            slug,
            visitorId
          );

        return json(
          request,
          data
        );
      }

      /*
       * -------------------------------------------------------
       * SUBMIT COMMENT
       * -------------------------------------------------------
       *
       * Turnstile is required here.
       *
       * Views and likes intentionally do not require Turnstile.
       */
      if (
        request.method === "POST" &&
        url.pathname ===
          "/api/comments"
      ) {
        const body =
          await request.json();

        const slug =
          cleanSlug(
            body.slug
          );

        const name =
          cleanText(
            body.name,
            80
          );

        const email =
          cleanText(
            body.email,
            254
          ).toLowerCase();

        const commentBody =
          cleanText(
            body.body,
            2000
          );

        const turnstileToken =
          cleanText(
            body.turnstileToken,
            4096
          );

        if (
          !slug ||
          !name ||
          !email ||
          !commentBody
        ) {
          return json(
            request,
            {
              error:
                "Name, email and comment are required.",
            },
            400
          );
        }

        const emailPattern =
          /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

        if (
          !emailPattern.test(
            email
          )
        ) {
          return json(
            request,
            {
              error:
                "Please enter a valid email address.",
            },
            400
          );
        }

        if (
          commentBody.length < 3
        ) {
          return json(
            request,
            {
              error:
                "Comment is too short.",
            },
            400
          );
        }

        /*
         * Verify Cloudflare Turnstile BEFORE
         * writing anything to D1.
         */
        const verification =
          await verifyTurnstile(
            request,
            env,
            turnstileToken
          );

        if (
          !verification.success
        ) {
          return json(
            request,
            {
              error:
                verification.error,
            },
            403
          );
        }

        /*
         * Only verified submissions reach D1.
         */
        await env
          .reliability_loop_db
          .prepare(`
            INSERT INTO comments (
              article_slug,
              name,
              email,
              body,
              status
            )
            VALUES (
              ?,
              ?,
              ?,
              ?,
              'pending'
            )
          `)
          .bind(
            slug,
            name,
            email,
            commentBody
          )
          .run();

        return json(
          request,
          {
            success: true,

            message:
              "Thanks! Your comment was submitted for review.",
          },
          201
        );
      }

      /*
       * -------------------------------------------------------
       * NOT FOUND
       * -------------------------------------------------------
       */
      return json(
        request,
        {
          error:
            "Not found.",
        },
        404
      );
    } catch (error) {
      console.error(error);

      return json(
        request,
        {
          error:
            "Something went wrong. Please try again.",
        },
        500
      );
    }
  },
};